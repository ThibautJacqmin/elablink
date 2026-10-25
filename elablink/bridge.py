# -*- coding: utf-8 -*-
"""Apply saved Labmate checkpoints to eLabFTW experiments.

This synchronous engine is used on the background worker by :class:`AsyncBridge`.
It does not register itself as a Labmate backend or modify acquisition objects.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

from .exceptions import DuplicateTitle


if TYPE_CHECKING:
    from .experiment import Experiment


PayloadBuilder = Callable[
    [Any, Tuple[str, ...], Optional[Mapping[str, Any]]], Dict[str, Any]
]
ExperimentResolver = Callable[[Any, Dict[str, Any]], Optional["Experiment"]]


class Bridge:
    """Create or update eLabFTW experiments from saved checkpoint payloads.

    Use :class:`AcquisitionAnalysisManager` or :class:`AsyncBridge` for normal
    Labmate use. This lower-level engine performs network requests synchronously;
    the asynchronous bridge calls it only on its worker.

    Args:
        client: :class:`Client` instance.
        payload_builder: Optional function receiving the saved checkpoint,
            attachment paths and metadata. Its payload may contain ``title``,
            ``body``, ``metadata``, ``category``, ``status``, ``tags``, ``steps``
            and ``comments``. Tags are added without removing existing tags.
        experiment_resolver: Optional function receiving the checkpoint and
            payload, returning an experiment or ``None`` to use normal creation.
    """

    def __init__(
        self,
        client: Any,
        *,
        payload_builder: Optional[PayloadBuilder] = None,
        experiment_resolver: Optional[ExperimentResolver] = None,
    ) -> None:
        self._client = client
        self._payload_builder = payload_builder or self._default_payload_builder
        self._experiment_resolver = experiment_resolver

    def save_experiment(
        self,
        acquisition: Any,
        *,
        attachments: Iterable[Union[str, Path]] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        experiment: Optional["Experiment"] = None,
    ) -> "Experiment":
        """Apply a saved checkpoint to a supplied, resolved or newly created experiment.

        Attachments must already exist locally. The asynchronous bridge supplies
        stable copies and retains ownership of experiment routing and caching.
        """
        paths = tuple(str(Path(path)) for path in attachments)
        payload = self._payload_builder(acquisition, paths, metadata)
        experiment = self._ensure_experiment(acquisition, payload, experiment)
        self._update_remote_experiment(experiment, payload, paths)
        return experiment

    @staticmethod
    def _default_payload_builder(
        acquisition: Any,
        attachments: Tuple[str, ...],
        metadata: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        payload = {"title": getattr(acquisition, "experiment_name", None)}
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        return payload

    def _ensure_experiment(
        self,
        acquisition: Any,
        payload: Mapping[str, Any],
        experiment: Optional["Experiment"],
    ) -> "Experiment":
        if experiment is not None:
            return experiment
        if self._experiment_resolver is not None:
            resolved = self._experiment_resolver(acquisition, payload)
            if resolved is not None:
                return resolved
        if self._client is None:
            raise RuntimeError("No eLabFTW client available to create experiments.")
        title = payload.get("title") or getattr(acquisition, "experiment_name", None)
        if not title:
            raise ValueError("Cannot determine the eLabFTW experiment title.")
        try:
            return self._client.create_experiment(title=title)
        except DuplicateTitle:
            return self._client.load_experiment(title=title)

    @staticmethod
    def _update_remote_experiment(
        experiment: "Experiment",
        payload: Mapping[str, Any],
        attachments: Tuple[str, ...],
    ) -> None:
        if payload.get("body") is not None:
            experiment.main_text = payload["body"]
        for field in ("metadata", "category", "status"):
            if field in payload and payload[field] is not None:
                setattr(experiment, field, payload[field])
        for field, method in (
            ("tags", "add_tag"),
            ("steps", "add_step"),
            ("comments", "add_comment"),
        ):
            for value in payload.get(field) or ():
                getattr(experiment, method)(value)
        for path in attachments:
            experiment.upload_file(path)


__all__ = ["Bridge"]
