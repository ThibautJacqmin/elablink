# -*- coding: utf-8 -*-
"""Labmate integration bridge for eLabFTW.

This module provides :class:`ElabBridge`, the synchronous integration engine
used by the asynchronous bridge, with compatibility for older Labmate backends.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union
import time

from .exceptions import DuplicateTitle

try:  # pragma: no cover - fallback for optional labmate dependency
    from labmate.acquisition.backend import AcquisitionBackend  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - labmate optional during tests

    class AcquisitionBackend:  # type: ignore[too-many-ancestors]
        """Lightweight stand-in used when labmate isn't available."""

        def save_snapshot(self, acquisition: Any) -> None:  # noqa: D401 - simple stub
            """Compatibility no-op."""

        def load_snapshot(self, acquisition: Any) -> None:  # noqa: D401 - simple stub
            """Compatibility no-op."""


if TYPE_CHECKING:  # pragma: no cover - typing only
    from .experiment import ElabExperiment
    from labmate.acquisition.acquisition_data import NotebookAcquisitionData


PayloadBuilder = Callable[
    ["NotebookAcquisitionData", Tuple[str, ...], Optional[Mapping[str, Any]]],
    Dict[str, Any],
]
ExperimentResolver = Callable[
    ["NotebookAcquisitionData", Dict[str, Any]], Optional["ElabExperiment"]
]


class ElabBridge(AcquisitionBackend):
    """Synchronise Labmate acquisitions with eLabFTW experiments.

    The bridge can be registered as a Labmate acquisition backend. It
    creates or resolves experiments, updates metadata, and uploads files.
    """

    def __init__(
        self,
        client: Any,
        *,
        payload_builder: Optional[PayloadBuilder] = None,
        experiment_resolver: Optional[ExperimentResolver] = None,
    ) -> None:
        """Initialize the bridge with a client and optional hooks.

        Args:
            client: :class:`ElabClient` instance (or a compatible API).
            payload_builder: Optional function to build a payload dict from an acquisition.
            experiment_resolver: Optional function to resolve an experiment manually.
        """
        self._client = client
        self._payload_builder = payload_builder or self._default_payload_builder
        self._experiment_resolver = experiment_resolver
        self._experiment_cache: Dict[int, tuple[Any, "ElabExperiment"]] = {}
        self._experiment_cache_by_identifier: Dict[str, "ElabExperiment"] = {}
        self._last_acquisition: Any = None

    def save_experiment(
        self,
        acquisition: "NotebookAcquisitionData",
        *,
        attachments: Iterable[Union[str, Path]] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        experiment: Optional["ElabExperiment"] = None,
    ) -> "ElabExperiment":
        """Create or update an experiment for a given acquisition."""
        self._last_acquisition = acquisition
        attachment_paths = tuple(str(Path(path)) for path in attachments)
        payload = self._payload_builder(acquisition, attachment_paths, metadata)
        identifier = self._resolve_experiment_identifier(acquisition, payload)
        if identifier:
            try:
                setattr(
                    acquisition, "_labmate_elabftw_experiment_identifier", identifier
                )
            except AttributeError:
                pass
        experiment = self._ensure_experiment(acquisition, payload, experiment)
        self._update_remote_experiment(experiment, payload, attachment_paths)
        return experiment

    def get_experiment(
        self,
        acquisition: Optional[Any] = None,
        *,
        wait: float = 0.1,
        poll_interval: float = 0.1,
    ) -> Optional["ElabExperiment"]:
        """
        Return the ElabExperiment linked to acquisition or analysis context.

        Notes:
            This compatibility API is synchronous and may contact the server.
            Its optional polling is useful when an external caller schedules saves.
            Current notebook integrations use ``aqm.experiment`` instead.

            * Set ``wait`` (seconds) to poll until the experiment becomes available.
            * If ``acquisition`` is None, use the most recently seen acquisition.
            * Accepted inputs are acquisition data, a manager, or analysis data.
        """
        source = acquisition
        acq = self._resolve_acquisition_source(source)
        if acq is not None:
            deadline = time.time() + max(wait, 0.0)
            while True:
                cached = self._get_cached_experiment(acq)
                if cached is not None:
                    return cached

                identifier = self._resolve_experiment_identifier(acq)
                if identifier:
                    loaded = self._load_experiment_by_title(identifier)
                    if loaded is not None:
                        self._store_cached_experiment(acq, loaded)
                        return loaded

                if time.time() >= deadline:
                    return None
                time.sleep(max(poll_interval, 0.01))

        analysis_data = self._resolve_analysis_source(source)
        if analysis_data is None:
            return None
        filepath = getattr(analysis_data, "filepath", None)
        if not filepath:
            return None
        title = Path(filepath).parent.name
        return self._load_experiment_by_title(title)

    def get_experiment_from_analysis(
        self, analysis_data: Any
    ) -> Optional["ElabExperiment"]:
        """
        Resolve experiment from analysis data by inferring title from parent folder name.
        Useful when `aqm.current_acquisition` is None (old-data analysis mode).
        """
        return self.get_experiment(analysis_data, wait=0.0)

    def _load_experiment_by_title(self, title: str) -> Optional["ElabExperiment"]:
        """Best-effort experiment loader by title."""
        if self._client is None:
            return None
        load_experiment = getattr(self._client, "load_experiment", None)
        if load_experiment is None:
            return None
        try:
            return load_experiment(title=title)
        except Exception:
            return None

    def _resolve_acquisition_source(
        self, source: Any
    ) -> Optional["NotebookAcquisitionData"]:
        """Normalize acquisition sources from Labmate objects."""
        if source is None:
            return self._last_acquisition
        if hasattr(source, "experiment_name") and hasattr(source, "filepath"):
            return source
        for attr in ("current_acquisition", "aq"):
            try:
                candidate = getattr(source, attr)
            except Exception:
                candidate = None
            if (
                candidate is not None
                and hasattr(candidate, "experiment_name")
                and hasattr(candidate, "filepath")
            ):
                return candidate
        return None

    def _resolve_analysis_source(self, source: Any) -> Optional[Any]:
        """Normalize analysis sources from Labmate objects."""
        if source is None:
            return None
        if hasattr(source, "filepath") and not hasattr(source, "experiment_name"):
            return source
        for attr in ("current_analysis", "data", "d"):
            try:
                candidate = getattr(source, attr)
            except Exception:
                candidate = None
            if candidate is not None and hasattr(candidate, "filepath"):
                return candidate
        return None

    def _default_payload_builder(
        self,
        acquisition: "NotebookAcquisitionData",
        attachments: Tuple[str, ...],
        metadata: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        """Build a minimal payload from an acquisition."""
        payload: Dict[str, Any] = {
            "title": getattr(acquisition, "experiment_name", None),
            "attachments": attachments,
        }
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        return payload

    def _ensure_experiment(
        self,
        acquisition: "NotebookAcquisitionData",
        payload: Dict[str, Any],
        experiment: Optional["ElabExperiment"],
    ) -> "ElabExperiment":
        """Return a resolved experiment (create or reuse)."""
        if experiment is not None:
            return experiment

        if self._experiment_resolver is not None:
            resolved = self._experiment_resolver(acquisition, payload)
            if resolved is not None:
                return resolved

        if self._client is None:
            raise RuntimeError("No ElabFTW client available to create experiments.")

        title = payload.get("title") or getattr(acquisition, "experiment_name", None)
        if not title:
            raise ValueError(
                "Cannot determine experiment title for ElabFTW experiment."
            )

        create_experiment = getattr(self._client, "create_experiment", None)
        if create_experiment is None:
            raise AttributeError(
                "Client does not provide a 'create_experiment' method."
            )

        try:
            experiment = create_experiment(title=title)
        except DuplicateTitle as exc:
            load_experiment = getattr(self._client, "load_experiment", None)
            if load_experiment is None:
                raise AttributeError(
                    "Client does not provide a 'load_experiment' method."
                ) from exc
            experiment = load_experiment(title=title)
            self._store_cached_experiment(acquisition, experiment)
            return experiment

        return experiment

    def _update_remote_experiment(
        self,
        experiment: "ElabExperiment",
        payload: Mapping[str, Any],
        attachments: Tuple[str, ...],
    ) -> None:
        """Apply payload fields and attachments to a remote experiment."""
        body = payload.get("body")
        if body is not None and hasattr(experiment, "main_text"):
            experiment.main_text = body

        if "metadata" in payload and payload["metadata"] is not None:
            experiment.metadata = payload["metadata"]

        category = payload.get("category")
        if category is not None and hasattr(experiment, "category"):
            experiment.category = category

        status = payload.get("status")
        if status is not None and hasattr(experiment, "status"):
            experiment.status = status

        tags = payload.get("tags")
        if tags is not None:
            for tag in tags:
                if hasattr(experiment, "add_tag"):
                    experiment.add_tag(tag)

        steps = payload.get("steps")
        if steps is not None and hasattr(experiment, "add_step"):
            for step in steps:
                experiment.add_step(step)

        comments = payload.get("comments")
        if comments is not None and hasattr(experiment, "add_comment"):
            for comment in comments:
                experiment.add_comment(comment)

        for path in attachments:
            # Avoid duplicates on rerun: skip if identical, otherwise replace.
            if hasattr(experiment, "upload_file"):
                experiment.upload_file(path)
            elif hasattr(experiment, "upsert_file"):
                experiment.upsert_file(path)
            else:
                experiment.add_file(path)

    def save_snapshot(self, acquisition: "NotebookAcquisitionData") -> None:
        """Save a snapshot of the acquisition to eLabFTW (attachments only)."""
        self._last_acquisition = acquisition
        attachments: Tuple[Path, ...] = ()
        filepath = getattr(acquisition, "filepath", None)
        if filepath:
            original_path = Path(filepath)
            resolved_path: Optional[Path] = None

            candidate = (
                original_path
                if original_path.suffix
                else original_path.with_suffix(".h5")
            )
            if candidate.exists():
                resolved_path = candidate
            else:
                search_dir = original_path.parent
                stem = original_path.stem
                suffix = original_path.suffix or ".h5"
                pattern = f"{stem}*{suffix}"
                newest_match: tuple[float, Path] | None = None
                if search_dir.exists():
                    for match in search_dir.glob(pattern):
                        if not match.is_file():
                            continue
                        try:
                            modified = match.stat().st_mtime
                        except OSError:
                            continue
                        if newest_match is None or modified > newest_match[0]:
                            newest_match = (modified, match)
                if newest_match is not None:
                    resolved_path = newest_match[1]

            if resolved_path is not None:
                attachments = (resolved_path,)
                figure_prefixes: list[str] = []
                stem_path = resolved_path.with_suffix("")
                stem_name = stem_path.name
                if stem_name:
                    figure_prefixes.append(stem_name)
                resolved_name = resolved_path.name
                if resolved_name and resolved_name not in figure_prefixes:
                    figure_prefixes.append(resolved_name)

                figure_attachments: dict[str, Path] = {}
                for prefix in figure_prefixes:
                    try:
                        candidates = resolved_path.parent.glob(f"{prefix}_FIG*")
                    except OSError:
                        continue
                    for candidate in candidates:
                        try:
                            if candidate.is_file():
                                candidate_key = str(candidate)
                                figure_attachments.setdefault(candidate_key, candidate)
                        except OSError:
                            continue
                if figure_attachments:
                    attachments = attachments + tuple(
                        sorted(figure_attachments.values(), key=lambda path: path.name)
                    )

        cached_experiment = self._get_cached_experiment(acquisition)
        experiment = self.save_experiment(
            acquisition,
            attachments=attachments,
            experiment=cached_experiment,
        )
        self._store_cached_experiment(acquisition, experiment)

    def _get_cached_experiment(
        self, acquisition: "NotebookAcquisitionData"
    ) -> Optional["ElabExperiment"]:
        """Return a cached experiment for the acquisition if available."""
        cached = getattr(acquisition, "_labmate_elabftw_experiment", None)
        if cached is not None:
            return cached

        cache_entry = self._experiment_cache.get(id(acquisition))
        if cache_entry is not None:
            cached_acquisition, experiment = cache_entry
            if cached_acquisition is acquisition:
                return experiment
            self._experiment_cache.pop(id(acquisition), None)

        identifier = self._resolve_experiment_identifier(acquisition)
        if identifier is not None:
            experiment = self._experiment_cache_by_identifier.get(identifier)
            if experiment is not None:
                return experiment

        return None

    def _store_cached_experiment(
        self,
        acquisition: "NotebookAcquisitionData",
        experiment: "ElabExperiment",
    ) -> None:
        """Cache the experiment on the acquisition object when possible."""
        self._last_acquisition = acquisition
        identifier = self._resolve_experiment_identifier(acquisition)
        if identifier is not None:
            self._experiment_cache_by_identifier[identifier] = experiment
            try:
                setattr(
                    acquisition, "_labmate_elabftw_experiment_identifier", identifier
                )
            except AttributeError:
                pass
        try:
            setattr(acquisition, "_labmate_elabftw_experiment", experiment)
        except AttributeError:
            self._experiment_cache[id(acquisition)] = (acquisition, experiment)
        else:
            self._experiment_cache.pop(id(acquisition), None)

    def _resolve_experiment_identifier(
        self,
        acquisition: "NotebookAcquisitionData",
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Optional[str]:
        """Resolve a stable experiment identifier for caching and lookup."""
        identifier = getattr(
            acquisition, "_labmate_elabftw_experiment_identifier", None
        )
        if identifier:
            return str(identifier)

        if payload is not None:
            title = payload.get("title")
            if title:
                return str(title)

        experiment_name = getattr(acquisition, "experiment_name", None)
        if experiment_name:
            return str(experiment_name)

        return None

    def load_snapshot(self, acquisition: "NotebookAcquisitionData") -> None:
        """Labmate hook: snapshots are not loaded from eLabFTW."""
        # The integration currently does not support loading snapshots.
        return None

    def ensure_local_file(self, local_path: str | Path) -> bool:
        """Ensure a requested attachment exists locally, downloading if needed."""
        local_path = Path(local_path)

        # Already present
        if local_path.exists():
            return True

        # Heuristic: infer experiment title from folder name (Experience 05)
        # and requested attachment name from the filename.
        exp_title = local_path.parent.name
        attachment_name = local_path.name

        # Load experiment (adapt to your client API)
        exp = self._client.load_experiment(title=exp_title)

        # Find matching attachment metadata.
        match = None
        if hasattr(exp, "get_file"):
            match = exp.get_file(attachment_name)
        elif hasattr(exp, "list_files"):
            files = exp.list_files()
            for f in files:
                fname = f["name"] if isinstance(f, dict) else getattr(f, "name", None)
                real_name = (
                    f.get("real_name")
                    if isinstance(f, dict)
                    else getattr(f, "real_name", None)
                )
                if attachment_name in {fname, real_name}:
                    match = f
                    break

        if match is None:
            return False

        # Download into the exact requested path
        local_path.parent.mkdir(parents=True, exist_ok=True)

        file_id = match["id"] if isinstance(match, dict) else getattr(match, "id", None)
        if file_id is None:
            return False
        exp.download_file(file_id=file_id, destination=local_path)

        return local_path.exists()


__all__ = ["ElabBridge"]
