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
from .remote_analysis import validate_request


class AcquisitionAnalysisManager(LabmateAcquisitionAnalysisManager):
    """Save locally with Labmate and upload checkpoints to eLabFTW asynchronously.

    Use exactly like Labmate's manager, selecting this class through the import::

        from elablink import AcquisitionAnalysisManager
        aqm = AcquisitionAnalysisManager("data")

    The name passed to ``acquisition_cell`` selects the remote experiment. Known
    uploaded/downloaded files keep their original experiment ID when reopened.
    Otherwise analyses follow the acquisition/folder name. ``experiment_title``
    overrides name-based routing; ``experiment_id`` fixes one existing experiment
    and rejects remote opens for another ID. Title reuse follows
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
        config_path: Optional[Union[str, Path]] = None,
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
            analysis_directory=Path(str(self.data_directory)) / ".elabftw",
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
        when another acquisition is selected. Save a checkpoint first, or reopen
        a remote/previously uploaded file whose experiment association is known.
        """
        self._elab_bridge._check_open()
        data = self.current_analysis
        if data is None:
            data = self._current_acquisition
        if data is None:
            raise RuntimeError("Save an acquisition before accessing aqm.experiment")
        return self._elab_bridge.experiment_for(data)

    def analysis_cell(
        self,
        filename=None,
        *,
        acquisition_name=None,
        cell=None,
        filepath=None,
        prerun=None,
        experiment_id: Optional[int] = None,
        download_timeout: Optional[float] = 120,
    ):
        """Analyse local data or explicitly download an eLabFTW HDF5 attachment.

        Use ``filepath=...`` for an existing local file, as with Labmate. Use
        ``experiment_id=123, filename="run.h5"`` to open an eLabFTW attachment.
        An associated local file is reused; missing files are downloaded.
        The remote form waits for the download before opening the data; figure
        saves and experiment edits still upload asynchronously to its original ID.
        Existing local copies are never overwritten. A local index (and a sidecar
        for downloads) preserves the experiment when reopening its local path.

        ``download_timeout`` limits the wait, not the HTTP request lifetime. A
        timeout leaves the download running without changing the current analysis.
        Check ``upload_status()`` and ``flush()`` for its eventual result/path.
        """
        if experiment_id is not None:
            if filepath is not None:
                raise ValueError("Use either filepath or experiment_id with filename")
            validate_request(experiment_id, filename)
            if download_timeout is not None and download_timeout <= 0:
                raise ValueError("download_timeout must be positive or None")
            future = self._elab_bridge.download_analysis(
                experiment_id, filename, Path(str(self.data_directory)) / ".elabftw"
            )
            try:
                information = future.result(timeout=download_timeout)
            except TimeoutError:
                if not future.done():
                    raise TimeoutError(
                        "The analysis download is still running; check upload_status() "
                        "or flush() before reopening its local file"
                    ) from None
                raise
            filepath = information["filepath"]
            filename = None
            self._elab_bridge.bind_analysis(filepath, future)
        elif filepath is not None or filename is not None:
            local_path = (
                filepath if filepath is not None else self._get_full_filename(filename)
            )
            self._elab_bridge.bind_analysis(local_path)
        return super().analysis_cell(
            filename,
            acquisition_name=acquisition_name,
            cell=cell,
            filepath=filepath,
            prerun=prerun,
        )

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
