"""Experiment-bound queued edits and automatic metadata reads.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Callable, Mapping, Union

from .step import Step


if TYPE_CHECKING:
    from .async_bridge import AsyncBridge


class AsyncExperiment:
    """Queue experiment edits without waiting for the eLabFTW server.

    Obtain a handle from ``aqm.experiment`` after saving a checkpoint or opening
    an analysis whose remote experiment association is known.
    It stays bound to that experiment even when another acquisition is selected.
    Methods return Futures; errors are also reported by ``aqm.flush()`` and
    ``aqm.close()``. Text assignment is queued too, not confirmed immediately.

    Reading metadata fetches it on the worker and waits for the result, including
    any earlier queued jobs. No explicit ``refresh()`` is needed. Text remains
    write-only. This is not a synchronous ``Experiment``.
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

    def _submit(
        self,
        operation: Callable[[Any], Any],
        label: str,
    ) -> Future:
        return self._bridge._submit_experiment_edit(
            self._key, self._title, self._prerequisite, operation, label
        )

    def _cache_fields(self, experiment: Any, fields: tuple[str, ...]) -> None:
        """Capture metadata on the worker and publish one complete snapshot."""
        values = {}
        for field in fields:
            value = getattr(experiment, "steps_by_id" if field == "steps" else field)
            if field == "steps":
                values[field] = tuple(
                    (step.ID, step.text, step.status) for step in value.values()
                )
            elif field in ("categories", "statuses", "available_tags"):
                values[field] = dict(value)
            else:
                values[field] = value
        with self._bridge._experiment_state_lock:
            previous = self._bridge._experiment_states.get(self._key, {})
            self._bridge._experiment_states[self._key] = {**previous, **values}

    def _read(self, field: str) -> Any:
        """Fetch only the requested metadata, keeping HTTP on the worker."""
        def read_metadata(experiment):
            if field in ("steps", "category", "status", "tags"):
                experiment.refresh()
            self._cache_fields(experiment, (field,))

        self._submit(read_metadata, f"read experiment {field}").result(timeout=120)
        with self._bridge._experiment_state_lock:
            return deepcopy(self._bridge._experiment_states[self._key][field])

    def refresh(self) -> Future:
        """Queue a metadata refresh without waiting for HTTP.

        The caller never waits for HTTP. Use ``aqm.flush()`` or this Future's
        ``result()`` when an explicit wait is appropriate. Property reads fetch
        automatically, so calling this method first is optional.
        """
        def refresh_metadata(experiment):
            experiment.refresh()
            self._cache_fields(
                experiment,
                ("steps", "categories", "statuses", "category", "status", "tags"),
            )

        return self._submit(refresh_metadata, "experiment metadata")

    @property
    def steps_by_id(self) -> dict[int, Step]:
        """Fetch steps keyed by ID, retaining repeated labels."""
        return {
            ID: Step(
                ID,
                text,
                status,
                lambda step_id, checked: self.check_step(step_id, checked=checked),
            )
            for ID, text, status in self._read("steps")
        }

    @property
    def steps(self) -> dict[str, Step]:
        """Fetch named steps whose checkbox assignments queue bound edits."""
        result = {}
        for step in self.steps_by_id.values():
            if step.text in result:
                raise ValueError(
                    f"Several steps match {step.text!r}; use steps_by_id"
                )
            result[step.text] = step
        return result

    @property
    def categories(self) -> dict[str, int]:
        """Fetch available category names and IDs, waiting for the server."""
        return self._read("categories")

    @property
    def statuses(self) -> dict[str, int]:
        """Fetch available status names and IDs, waiting for the server."""
        return self._read("statuses")

    @property
    def tags(self) -> list[str]:
        """Fetch the tags attached to this experiment."""
        return self._read("tags")

    @property
    def available_tags(self) -> dict[str, int]:
        """Fetch the team's available tag names and IDs."""
        return self._read("available_tags")

    @property
    def category(self) -> str | int | None:
        """Fetch this experiment's current category."""
        return self._read("category")

    @category.setter
    def category(self, value: str | int) -> None:
        self._submit(
            lambda experiment: setattr(experiment, "category", value),
            "experiment category",
        )

    @property
    def status(self) -> str | int | None:
        """Fetch this experiment's current status."""
        return self._read("status")

    @status.setter
    def status(self, value: str | int) -> None:
        self._submit(
            lambda experiment: setattr(experiment, "status", value),
            "experiment status",
        )

    def add_step(self, text: str, *, checked: bool = False) -> Future:
        """Queue a step after this handle's acquisition or analysis is ready."""
        if not isinstance(checked, bool):
            raise TypeError("checked must be a boolean")

        def add(experiment):
            if checked:
                experiment.add_step(text, checked=True)
            else:
                experiment.add_step(text)

        return self._submit(add, "experiment step")

    def check_step(
        self,
        text_or_ID: str | int,
        *,
        checked: bool = True,
        occurrence: int | None = None,
    ) -> Future:
        """Queue checking or unchecking an existing step, without creating it."""
        if not isinstance(checked, bool):
            raise TypeError("checked must be a boolean")
        return self._submit(
            lambda experiment: experiment.check_step(
                text_or_ID, checked=checked, occurrence=occurrence
            ),
            "experiment step checkbox",
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
