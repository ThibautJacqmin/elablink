"""Labmate's familiar manager interface with background eLabFTW uploads.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from labmate.acquisition_notebook import (
    AcquisitionAnalysisManager as LabmateAcquisitionAnalysisManager,
)

from .async_bridge import AsyncBridge
from .async_experiment import AsyncExperiment


class AcquisitionAnalysisManager(LabmateAcquisitionAnalysisManager):
    """Save locally with Labmate and upload checkpoints to eLabFTW asynchronously.

    Use exactly like Labmate's manager, selecting this class through the import::

        from elablink import AcquisitionAnalysisManager
        aqm = AcquisitionAnalysisManager("data")

    The name passed to ``acquisition_cell`` selects the remote experiment. Reopened
    analyses use their data folder name. ``experiment_title`` overrides that routing;
    ``experiment_id`` sends everything to one existing experiment. Title reuse follows
    ``UNIQUE_EXPERIMENTS_TITLES`` in ``elablink.conf``.

    Standard Labmate keyword arguments are forwarded unchanged. Additional options
    configure :class:`AsyncBridge`; the spool defaults to ``.upload_queue`` inside
    the local data directory. Network operations run on one worker, not in save cells.

    Call ``close()`` before restarting the kernel to finish uploads and report errors.
    Failed snapshots remain in the spool; they are not automatically retried. Create
    a new manager after closing. No Python cleanup can guarantee delivery on a crash.
    """

    def __init__(
        self,
        data_directory: Any = None,
        *,
        experiment_title: Optional[str] = None,
        experiment_id: Optional[int] = None,
        config_path: Union[str, Path] = "elablink.conf",
        spool_directory: Optional[Union[str, Path]] = None,
        request_timeout: Tuple[float, float] = (5, 30),
        client_factory: Optional[Callable[[], Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data_directory, **kwargs)
        if spool_directory is None:
            spool_directory = Path(str(self.data_directory)) / ".upload_queue"
        self._elab_bridge = AsyncBridge(
            experiment_title,
            config_path=config_path,
            spool_directory=spool_directory,
            experiment_id=experiment_id,
            request_timeout=request_timeout,
            client_factory=client_factory,
        )
        try:
            self._elab_bridge.attach(self)
        except Exception:
            self._elab_bridge.close()
            raise

    @property
    def experiment(self) -> AsyncExperiment:
        """Access queued edits for the current acquisition or analysis's experiment.

        The handle does not wait for the server, and stays bound to its experiment
        when another acquisition is selected. This manager must have saved at least
        one acquisition or figure for that experiment first.
        """
        self._elab_bridge._check_open()
        data = self.current_analysis
        if data is None:
            data = self._current_acquisition
        if data is None:
            raise RuntimeError("Save an acquisition before accessing aqm.experiment")
        return self._elab_bridge.experiment_for(data)

    def upload_status(self) -> List[Dict[str, Any]]:
        """Inspect file uploads and experiment edits without contacting the server."""
        return self._elab_bridge.status()

    def flush(self, timeout: Optional[float] = 120) -> Dict[str, Any]:
        """Wait for uploads and edits; return the last job's experiment ID, title and URL.

        The manager remains usable. A timeout leaves jobs running; failures are
        reported without removing their local snapshots.
        """
        return self._elab_bridge.flush(timeout=timeout)

    def close(self, timeout: Optional[float] = 120) -> None:
        """Finish uploads and edits, including after an earlier close timed out."""
        try:
            self._elab_bridge.close(timeout=timeout)
        finally:
            super().close()

    def __enter__(self) -> AcquisitionAnalysisManager:
        self._elab_bridge.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return self._elab_bridge.__exit__(exc_type, exc_value, traceback)
        finally:
            super().close()
