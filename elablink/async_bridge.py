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

from ._paths import absolute_storage_path
from .async_experiment import AsyncExperiment
from .bridge import Bridge
from .client import Client
from .config import resolve_config_path
from .exceptions import safe_error_message
from .remote_analysis import (
    RemoteSource,
    check_source_manifest,
    download_analysis,
    read_source,
    remember_source,
    validate_request,
    write_source_manifest,
)


logger = logging.getLogger(__name__)


@dataclass
class _Snapshot:
    """Detached input for the synchronous bridge's default payload builder."""

    filepath: str
    experiment_name: str
    experiment_id: Optional[int] = None
    server: Optional[str] = None
    original_filepath: Optional[str] = None


@dataclass
class _Job:
    label: str
    future: Future


def _future_error(future: Future) -> Optional[BaseException]:
    if future.cancelled():
        return CancelledError("Job was cancelled")
    return future.exception() if future.done() else None


def _check_source_path(path: Path) -> None:
    """Do not follow links while capturing files from an acquisition folder."""
    for entry in (path, *path.parents):
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Acquisition attachments must not use symbolic links or junctions.")


def _set_request_timeouts(
    client: Any, timeout: Tuple[float, float], upload_timeout: Tuple[float, float] = (300, 300)
) -> None:
    """Set defaults on this client only, preserving explicit request overrides."""
    original = client.api_client.call_api

    @wraps(original)
    def call_api(*args, **kwargs):
        if kwargs.get("_request_timeout") is None:
            # urllib3 uses the connection timeout while sending the body too.
            kwargs["_request_timeout"] = upload_timeout if kwargs.get("files") else timeout
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
        upload_timeout: HTTP connection/send and read timeouts for multipart uploads.
        client_factory: Optional factory for a compatible client, called on the worker.
            Custom factories are responsible for their own transport timeouts.
        analysis_directory: Local origin index and downloaded-file directory.
            Defaults to ``.elabftw`` beside the upload queue.

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
        upload_timeout: Tuple[float, float] = (300, 300),
        client_factory: Optional[Callable[[], Any]] = None,
        analysis_directory: Optional[Union[str, Path]] = None,
    ) -> None:
        if title is not None and not title.strip():
            raise ValueError("An experiment title is required")
        if len(request_timeout) != 2 or any(value <= 0 for value in request_timeout):
            raise ValueError("HTTP connection and read timeouts must be positive")
        if len(upload_timeout) != 2 or any(value <= 0 for value in upload_timeout):
            raise ValueError("Upload connection/send and read timeouts must be positive")
        self.title = title
        if client_factory is None:
            self.config_path = resolve_config_path(config_path)
        else:
            self.config_path = (
                Path(config_path).expanduser().resolve()
                if config_path is not None
                else None
            )
        self.spool_directory = Path(spool_directory).expanduser().resolve()
        self._analysis_directory = (
            Path(analysis_directory).expanduser().resolve()
            if analysis_directory is not None
            else self.spool_directory.parent / ".elabftw"
        )
        self.spool_directory.mkdir(parents=True, exist_ok=True)
        self.experiment_id = experiment_id
        self._request_timeout = request_timeout
        self._upload_timeout = upload_timeout
        self._client_factory = client_factory
        self._client = None
        self._bridge = None
        self._experiments: Dict[Union[str, int], Any] = {}
        self._experiment_states: Dict[Union[str, int], Dict[str, Any]] = {}
        self._experiment_state_lock = threading.Lock()
        self._experiment_handles: Dict[Union[str, int], AsyncExperiment] = {}
        self._analysis_sources: Dict[str, RemoteSource] = {}
        self._analysis_handles: Dict[str, AsyncExperiment] = {}
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
        methods = ("add_acquisition_saved", "add_figure_saved")
        if not all(callable(getattr(hooks, method, None)) for method in methods):
            raise TypeError(
                "Labmate LifecycleHooks with figure_saved(AnalysisData) are required"
            )
        hooks.add_acquisition_saved(self._on_acquisition_saved)
        hooks.add_figure_saved(self._on_figure_saved)
        self._attached = True

    def _on_acquisition_saved(self, acquisition: Any, **kwargs: Any) -> None:
        self.enqueue(acquisition, label="acquisition")

    def _on_figure_saved(self, analysis: Any, **kwargs: Any) -> None:
        self.enqueue(analysis, label="analysis + figure")

    def _title_for(self, data: Any) -> str:
        source = self._analysis_sources.get(self._prefix(data.filepath))
        if source is not None:
            return source.title
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

    @staticmethod
    def _prefix(filepath: Union[str, Path]) -> str:
        path = str(Path(filepath).expanduser().resolve())
        return path[:-3] if path.endswith(".h5") else path

    def download_analysis(
        self, experiment_id: int, filename: str, directory: Union[str, Path]
    ) -> Future:
        """Queue reuse or download of one remote HDF5 attachment's local copy."""
        self._check_open()
        validate_request(experiment_id, filename)
        if self.experiment_id is not None and self.experiment_id != experiment_id:
            raise ValueError(
                "The requested experiment conflicts with the manager's experiment_id"
            )
        directory = Path(directory).expanduser().resolve()
        future = self._worker.submit(
            self._download_analysis, experiment_id, filename, directory
        )
        self._jobs.append(_Job("download analysis", future))
        return future

    def _download_analysis(self, experiment_id: int, filename: str, directory: Path):
        self._ensure_client()
        filepath, source, experiment = download_analysis(
            self._client, experiment_id, filename, directory
        )
        self._experiments[experiment_id] = experiment
        result = self._experiment_info(experiment, source.title)
        result["filepath"] = str(filepath)
        return result

    def bind_analysis(
        self, filepath: Union[str, Path], prerequisite: Optional[Future] = None
    ):
        """Remember an attachment's origin without accessing the server."""
        self._check_open()
        source = read_source(Path(filepath), self._analysis_directory)
        if source is None:
            return
        if (
            self.experiment_id is not None
            and self.experiment_id != source.experiment_id
        ):
            raise ValueError("The local file belongs to another experiment_id")
        prefix = self._prefix(filepath)
        self._analysis_sources[prefix] = source
        if prerequisite is not None:
            handle = AsyncExperiment(
                self, source.experiment_id, source.title, prerequisite
            )
            self._analysis_handles[prefix] = handle
            self._latest_experiment = handle

    def _prepare_analysis(self, snapshot: _Snapshot):
        experiment = self._ensure_remote(snapshot)
        return self._experiment_info(experiment, snapshot.experiment_name)

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
        prefix = self._prefix(data.filepath)
        source = self._analysis_sources.get(prefix)
        if source is not None:
            if prefix not in self._analysis_handles:
                snapshot = _Snapshot(
                    prefix, source.title, source.experiment_id, source.server
                )
                future = self._worker.submit(self._prepare_analysis, snapshot)
                self._jobs.append(_Job("open analysis experiment", future))
                self._analysis_handles[prefix] = AsyncExperiment(
                    self, source.experiment_id, source.title, future
                )
            return self._analysis_handles[prefix]
        key = self._experiment_key(self._title_for(data))
        if key not in self._experiment_handles:
            raise RuntimeError(
                "Save this acquisition or a figure before accessing its experiment"
            )
        return self._experiment_handles[key]

    def enqueue(self, data: Any, *, label: str = "snapshot") -> Future:
        """Capture a saved acquisition or analysis locally and queue its upload."""
        self._check_open()
        original = absolute_storage_path(str(data.filepath))
        h5_path = original if str(original).endswith(".h5") else Path(str(original) + ".h5")
        _check_source_path(h5_path)
        prefix = h5_path.resolve()
        prefix = Path(str(prefix)[:-3])
        if not h5_path.is_file():
            raise FileNotFoundError(h5_path)
        self.bind_analysis(h5_path)
        title = self._title_for(data)
        remote_source = self._analysis_sources.get(str(prefix))
        directory = Path(mkdtemp(prefix="snapshot-", dir=self.spool_directory))
        try:
            files = [path for path in prefix.parent.iterdir() if path.is_file()]
            stems = [path.name[:-3] for path in files if path.name.endswith(".h5")]
            sources = []
            for source in sorted(files):
                owners = [
                    stem
                    for stem in stems
                    if source.name == stem + ".h5"
                    or source.name.startswith(stem + "_")
                    or source.name.startswith(stem + ".h5_")
                ]
                if owners and max(owners, key=len) == prefix.name:
                    _check_source_path(source)
                    sources.append(source)
            attachments = []
            for source in sources:
                target = directory / source.name
                shutil.copy2(source, target)
                attachments.append(target)
            snapshot = _Snapshot(
                str(directory / prefix.name),
                title,
                remote_source.experiment_id if remote_source else None,
                remote_source.server if remote_source else None,
                str(prefix) + ".h5",
            )
            future = self._worker.submit(
                self._upload, snapshot, tuple(attachments), directory
            )
        except Exception:
            self._remove_snapshot(directory)
            raise
        self.latest_snapshot = future
        key = (
            remote_source.experiment_id
            if remote_source
            else self._experiment_key(title)
        )
        handle = AsyncExperiment(self, key, title, future)
        self._experiment_handles[key] = handle
        if remote_source is not None:
            self._analysis_handles[str(prefix)] = handle
        self._latest_experiment = handle
        self._jobs.append(_Job(label, future))
        return future

    def _ensure_client(self) -> None:
        if self._client is None:
            if self._client_factory is None:
                self._client = Client(str(self.config_path))
                _set_request_timeouts(self._client, self._request_timeout, self._upload_timeout)
            else:
                self._client = self._client_factory()
        if self._bridge is None:
            self._bridge = Bridge(self._client)

    def _ensure_remote(self, snapshot: _Snapshot) -> Any:
        self._ensure_client()
        if snapshot.server is not None:
            server = self._client.configuration.host.rstrip("/")
            if server != snapshot.server:
                raise ValueError(
                    "The downloaded file belongs to a different eLabFTW server"
                )
        experiment_id = (
            snapshot.experiment_id
            if snapshot.experiment_id is not None
            else self.experiment_id
        )
        key = experiment_id if experiment_id is not None else snapshot.experiment_name
        if key not in self._experiments:
            if experiment_id is not None:
                experiment = self._client.load_experiment(ID=experiment_id)
            else:
                experiment = self._bridge.save_experiment(snapshot)
            self._experiments[key] = experiment
        return self._experiments[key]

    def _upload(
        self, snapshot: _Snapshot, attachments: Tuple[Path, ...], directory: Path
    ):
        original = Path(snapshot.original_filepath) if snapshot.original_filepath is not None else None
        self._ensure_client()
        known = read_source(original, self._analysis_directory) if original is not None else None
        server = self._client.configuration.host.rstrip("/")
        requested_id = snapshot.experiment_id if snapshot.experiment_id is not None else self.experiment_id
        if known is not None:
            if known.server.rstrip("/") != server:
                raise ValueError("The local file belongs to a different eLabFTW server")
            if requested_id is None:
                # An earlier queued save can legitimately have recorded this
                # association. Never create a new record by title to check it.
                previous = self._experiments.get(snapshot.experiment_name)
                if previous is None:
                    raise ValueError(
                        "The local file's origin changed after capture. "
                        "Check its eLabFTW association before queuing a new save."
                    )
                requested_id = previous.ID
            if known.experiment_id != requested_id:
                raise ValueError("The local file belongs to a different eLabFTW experiment")
        experiment = self._ensure_remote(snapshot)
        source = RemoteSource(experiment.ID, snapshot.experiment_name, server)
        if original is not None:
            if known is not None and known.experiment_id != experiment.ID:
                raise ValueError("The local file belongs to a different eLabFTW experiment")
            check_source_manifest(original, source)
        self._bridge.save_experiment(
            snapshot, attachments=attachments, experiment=experiment
        )
        result = self._experiment_info(experiment, snapshot.experiment_name)
        if original is not None:
            # Labmate can replace the local H5 after the checkpoint has uploaded.
            write_source_manifest(original, source, missing_ok=True)
            remember_source(original, source, self._analysis_directory)
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

    def add_step(self, text: str, *, checked: bool = False) -> Future:
        """Queue a step after successful completion of the latest snapshot."""
        return self.experiment.add_step(text, checked=checked)

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
                    "error": safe_error_message(error) if error else None,
                }
            )
        return rows

    def flush(self, timeout: Optional[float] = 120) -> Dict[str, Any]:
        """Wait for submitted jobs and return the last job's experiment information.

        A timeout leaves jobs running; it does not cancel HTTP requests. Job
        failures raise a RuntimeError with a safe summary. Failed upload
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
            cause = failures[0]
            message = safe_error_message(cause)
            if (message != str(cause) or cause.__cause__ is not None or cause.__context__ is not None
                    or hasattr(cause, "status") or (hasattr(cause, "headers") and hasattr(cause, "body"))):
                cause = None
            raise RuntimeError(
                f"{len(failures)} eLabFTW jobs failed; "
                f"{message} "
                f"Any failed upload snapshots remain in {self.spool_directory}"
            ) from cause
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
            except Exception as error:
                logger.error(
                    "Background uploads also failed while leaving the context: %s",
                    safe_error_message(error),
                )
        return False
