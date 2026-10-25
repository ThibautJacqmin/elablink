"""Asynchronous Labmate acquisition and figure uploads to eLabFTW.

@author Thibaut Jacqmin
"""

from __future__ import annotations

import logging
import shutil
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .async_experiment import AsyncExperiment
from .bridge import Bridge
from .client import Client
from .config import resolve_config_path


logger = logging.getLogger(__name__)


@dataclass
class _Snapshot:
    """Detached input for the synchronous bridge's default payload builder."""

    filepath: str
    experiment_name: str


@dataclass
class _Job:
    label: str
    future: Future


def _future_error(future: Future) -> Optional[BaseException]:
    if future.cancelled():
        return CancelledError("Job was cancelled")
    return future.exception() if future.done() else None


def _set_request_timeouts(client: Any, timeout: Tuple[float, float]) -> None:
    """Set defaults on this client only, preserving explicit request overrides."""
    original = client.api_client.call_api

    @wraps(original)
    def call_api(*args, **kwargs):
        if kwargs.get("_request_timeout") is None:
            kwargs["_request_timeout"] = timeout
        return original(*args, **kwargs)

    client.api_client.call_api = call_api


class AsyncBridge:
    """Upload Labmate save checkpoints on one background worker.

    Args:
        title: Optional fixed eLabFTW experiment title. Otherwise use the acquisition
            name, or the data folder name for reopened analyses. Existing titles are
            reused when UNIQUE_EXPERIMENTS_TITLES is enabled in the configuration.
        config_path: Optional explicit Client configuration file. Otherwise use
            the root project's configuration setting, resolved before queueing.
        spool_directory: Local directory used for stable copies of files to upload.
        experiment_id: Existing experiment ID to use instead of resolving the title.
        request_timeout: Default HTTP connection and read timeouts, in seconds.
        client_factory: Optional factory for a compatible client, called on the worker.
            Custom factories are responsible for their own transport timeouts.

    Attach to a manager or its LifecycleHooks. Local saves briefly copy files into
    the spool; network operations run in order on one worker. Submit and flush from
    the creating notebook thread. Other writers must not modify files during capture.

    Failed snapshots remain in the spool. This bridge does not automatically retry
    or replay uploads after a restart. Closing stops new submissions; hooks remain
    registered, so create a fresh manager before attaching a replacement bridge.
    """

    def __init__(
        self,
        title: Optional[str] = None,
        *,
        config_path: Optional[Union[str, Path]] = None,
        spool_directory: Union[str, Path] = "data/.upload_queue",
        experiment_id: Optional[int] = None,
        request_timeout: Tuple[float, float] = (5, 30),
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        if title is not None and not title.strip():
            raise ValueError("An experiment title is required")
        if len(request_timeout) != 2 or any(value <= 0 for value in request_timeout):
            raise ValueError("HTTP connection and read timeouts must be positive")
        self.title = title
        if client_factory is None:
            self.config_path = resolve_config_path(config_path)
        else:
            self.config_path = (
                Path(config_path).expanduser().resolve()
                if config_path is not None else None
            )
        self.spool_directory = Path(spool_directory).expanduser().resolve()
        self.spool_directory.mkdir(parents=True, exist_ok=True)
        self.experiment_id = experiment_id
        self._request_timeout = request_timeout
        self._client_factory = client_factory
        self._client = None
        self._bridge = None
        self._experiments: Dict[Union[str, int], Any] = {}
        self._experiment_handles: Dict[Union[str, int], AsyncExperiment] = {}
        self._latest_experiment: Optional[AsyncExperiment] = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="elabftw")
        self._owner = threading.get_ident()
        self._closed = False
        self._attached = False
        self._jobs: List[_Job] = []
        self.latest_snapshot: Optional[Future] = None

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("Call this method from the creating notebook thread")

    def _check_open(self) -> None:
        self._check_owner()
        if self._closed:
            raise RuntimeError("This bridge is closed; create a new bridge and manager")

    def attach(self, manager_or_hooks: Any) -> None:
        """Register acquisition and analysis-figure callbacks exactly once."""
        self._check_open()
        if self._attached:
            raise RuntimeError("This bridge is already attached")
        hooks = getattr(manager_or_hooks, "hooks", manager_or_hooks)
        methods = ("add_acquisition_saved", "add_analysis_figure_saved")
        if not all(callable(getattr(hooks, method, None)) for method in methods):
            raise TypeError(
                "Labmate LifecycleHooks with analysis_figure_saved are required"
            )
        hooks.add_acquisition_saved(self._on_acquisition_saved)
        hooks.add_analysis_figure_saved(self._on_analysis_figure_saved)
        self._attached = True

    def _on_acquisition_saved(self, acquisition: Any, **kwargs: Any) -> None:
        self.enqueue(acquisition, label="acquisition")

    def _on_analysis_figure_saved(self, analysis: Any, **kwargs: Any) -> None:
        self.enqueue(analysis, label="analysis + figure")

    def _title_for(self, data: Any) -> str:
        title = (
            self.title
            or getattr(data, "experiment_name", None)
            or Path(str(data.filepath)).resolve().parent.name
        )
        if not title.strip():
            raise ValueError("Cannot determine the eLabFTW experiment title")
        return title

    def _experiment_key(self, title: str) -> Union[str, int]:
        return self.experiment_id if self.experiment_id is not None else title

    @property
    def experiment(self) -> AsyncExperiment:
        """Return a bound editing handle for the most recently submitted checkpoint."""
        self._check_open()
        if self._latest_experiment is None:
            raise RuntimeError("Save an acquisition before accessing the experiment")
        return self._latest_experiment

    def experiment_for(self, data: Any) -> AsyncExperiment:
        """Return a bound editing handle for this acquisition or analysis context."""
        self._check_open()
        key = self._experiment_key(self._title_for(data))
        if key not in self._experiment_handles:
            raise RuntimeError(
                "Save this acquisition or a figure before accessing its experiment"
            )
        return self._experiment_handles[key]

    def enqueue(self, data: Any, *, label: str = "snapshot") -> Future:
        """Capture a saved acquisition or analysis locally and queue its upload."""
        self._check_open()
        prefix = Path(str(data.filepath)).resolve()
        if str(prefix).endswith(".h5"):
            prefix = Path(str(prefix)[:-3])
        h5_path = Path(str(prefix) + ".h5")
        if not h5_path.is_file():
            raise FileNotFoundError(h5_path)
        title = self._title_for(data)
        directory = Path(mkdtemp(prefix="snapshot-", dir=self.spool_directory))
        try:
            files = [path for path in prefix.parent.iterdir() if path.is_file()]
            stems = [path.name[:-3] for path in files if path.name.endswith(".h5")]
            attachments = []
            for source in sorted(files):
                owners = [
                    stem
                    for stem in stems
                    if source.name == stem + ".h5"
                    or source.name.startswith(stem + "_")
                    or source.name.startswith(stem + ".h5_")
                ]
                if owners and max(owners, key=len) == prefix.name:
                    target = directory / source.name
                    shutil.copy2(source, target)
                    attachments.append(target)
            snapshot = _Snapshot(str(directory / prefix.name), title)
            future = self._worker.submit(
                self._upload, snapshot, tuple(attachments), directory
            )
        except Exception:
            self._remove_snapshot(directory)
            raise
        self.latest_snapshot = future
        key = self._experiment_key(title)
        handle = AsyncExperiment(self, key, title, future)
        self._experiment_handles[key] = handle
        self._latest_experiment = handle
        self._jobs.append(_Job(label, future))
        return future

    def _ensure_remote(self, snapshot: _Snapshot) -> Any:
        if self._client is None:
            if self._client_factory is None:
                self._client = Client(str(self.config_path))
                _set_request_timeouts(self._client, self._request_timeout)
            else:
                self._client = self._client_factory()
        if self._bridge is None:
            self._bridge = Bridge(self._client)
        key = self._experiment_key(snapshot.experiment_name)
        if key not in self._experiments:
            if self.experiment_id is not None:
                experiment = self._client.load_experiment(ID=self.experiment_id)
            else:
                experiment = self._bridge.save_experiment(snapshot)
            self._experiments[key] = experiment
        return self._experiments[key]

    def _upload(
        self, snapshot: _Snapshot, attachments: Tuple[Path, ...], directory: Path
    ):
        experiment = self._ensure_remote(snapshot)
        self._bridge.save_experiment(
            snapshot, attachments=attachments, experiment=experiment
        )
        result = self._experiment_info(experiment, snapshot.experiment_name)
        self._remove_snapshot(directory)
        return result

    def _remove_snapshot(self, directory: Path) -> None:
        resolved = directory.resolve()
        if resolved.parent != self.spool_directory or not resolved.name.startswith(
            "snapshot-"
        ):
            raise ValueError(
                "Refusing to remove files outside this bridge's snapshot folder"
            )
        shutil.rmtree(resolved)

    def _experiment_info(self, experiment: Any, title: str) -> Dict[str, Any]:
        host = self._client.configuration.host.rstrip("/")
        if host.endswith("/api/v2"):
            host = host[:-7]
        return {
            "id": experiment.ID,
            "url": f"{host}/experiments.php?mode=view&id={experiment.ID}",
            "title": title,
        }

    def add_step(self, text: str) -> Future:
        """Queue a step after successful completion of the latest snapshot."""
        return self.experiment.add_step(text)

    def _submit_experiment_edit(
        self,
        key: Union[str, int],
        title: str,
        prerequisite: Future,
        operation: Callable[[Any], Any],
        label: str,
    ) -> Future:
        self._check_open()

        def apply_edit():
            prerequisite.result()
            experiment = self._experiments[key]
            operation(experiment)
            return self._experiment_info(experiment, title)

        future = self._worker.submit(apply_edit)
        self._jobs.append(_Job(label, future))
        return future

    def status(self) -> List[Dict[str, Any]]:
        """Return job labels, pending/done/failed states and errors without network I/O."""
        rows = []
        for job in tuple(self._jobs):
            error = _future_error(job.future)
            state = "failed" if error else "done" if job.future.done() else "pending"
            rows.append(
                {
                    "job": job.label,
                    "state": state,
                    "error": str(error) if error else None,
                }
            )
        return rows

    def flush(self, timeout: Optional[float] = 120) -> Dict[str, Any]:
        """Wait for submitted jobs and return the last job's experiment information.

        A timeout leaves jobs running; it does not cancel HTTP requests. Job
        failures raise RuntimeError chained from the original error. Failed upload
        snapshots are retained; queued experiment edits are not persisted or retried.
        """
        self._check_owner()
        jobs = tuple(self._jobs)
        _, pending = wait(
            [job.future for job in jobs if not job.future.cancelled()], timeout=timeout
        )
        pending = {future for future in pending if not future.cancelled()}
        if pending:
            raise TimeoutError(
                f"{len(pending)} jobs still pending; call status() to inspect them"
            )
        failures = [
            error for job in jobs if (error := _future_error(job.future)) is not None
        ]
        if failures:
            raise RuntimeError(
                f"{len(failures)} eLabFTW jobs failed; "
                f"any failed upload snapshots remain in {self.spool_directory}"
            ) from failures[0]
        return jobs[-1].future.result() if jobs else {}

    def close(self, timeout: Optional[float] = 120) -> None:
        """Stop submissions and drain pending work, including after an earlier timeout."""
        self._check_owner()
        self._closed = True
        try:
            self.flush(timeout=timeout)
        finally:
            self._worker.shutdown(wait=False)

    def __enter__(self) -> AsyncBridge:
        self._check_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is None:
            self.close()
        else:
            try:
                self.close()
            except Exception:
                logger.exception(
                    "Background uploads also failed while leaving the context"
                )
        return False
