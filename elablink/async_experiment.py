"""A small, write-only handle for queued edits to an eLabFTW experiment.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Callable, Mapping, Union


if TYPE_CHECKING:
    from .async_bridge import AsyncBridge


class AsyncExperiment:
    """Queue experiment edits without waiting for the eLabFTW server.

    Obtain a handle from ``aqm.experiment`` after saving a checkpoint or opening
    an analysis whose remote experiment association is known.
    It stays bound to that experiment even when another acquisition is selected.
    Methods return Futures; errors are also reported by ``aqm.flush()`` and
    ``aqm.close()``. Text assignment is queued too, not confirmed immediately.

    Only the documented writes are supported. This is not a synchronous
    ``Experiment``: reading remote properties would require a network wait.
    Pending edits live in memory and are not replayed after a kernel restart.
    """

    __slots__ = ("_bridge", "_key", "_title", "_prerequisite")

    def __init__(
        self,
        bridge: AsyncBridge,
        key: Union[str, int],
        title: str,
        prerequisite: Future,
    ) -> None:
        self._bridge = bridge
        self._key = key
        self._title = title
        self._prerequisite = prerequisite

    def _submit(self, operation: Callable[[Any], Any], label: str) -> Future:
        return self._bridge._submit_experiment_edit(
            self._key, self._title, self._prerequisite, operation, label
        )

    def add_step(self, text: str) -> Future:
        """Queue a step after this handle's acquisition or analysis is ready."""
        return self._submit(
            lambda experiment: experiment.add_step(text), "experiment step"
        )

    def add_comment(self, text: str) -> Future:
        """Queue a comment on this experiment."""
        return self._submit(
            lambda experiment: experiment.add_comment(text), "experiment comment"
        )

    def add_tag(self, tag: str) -> Future:
        """Queue a tag on this experiment."""
        return self._submit(
            lambda experiment: experiment.add_tag(tag), "experiment tag"
        )

    @property
    def main_text(self) -> str:
        """Write-only experiment text; reading fresh server data is not implicit."""
        raise AttributeError(
            "main_text is write-only on an async experiment handle; "
            "reading fresh server data requires synchronous Client access"
        )

    @main_text.setter
    def main_text(self, text: str) -> None:
        self._submit(
            lambda experiment: setattr(experiment, "main_text", text), "experiment text"
        )

    @property
    def body(self) -> str:
        """Write-only alias for main_text."""
        return self.main_text

    @body.setter
    def body(self, text: str) -> None:
        self.main_text = text

    def set_extra_fields(
        self, fields: Mapping[str, Any], *, merge: bool = True
    ) -> Future:
        """Queue custom fields, capturing their current values before submission."""
        self._bridge._check_open()
        captured = deepcopy(dict(fields))
        return self._submit(
            lambda experiment: experiment.set_extra_fields(captured, merge=merge),
            "experiment extra fields",
        )
