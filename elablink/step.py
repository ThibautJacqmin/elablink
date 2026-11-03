"""A server-backed experiment or resource step.

@author Thibaut Jacqmin
"""

from concurrent.futures import Future
from typing import Any, Callable


class Step:
    """A named step with a cached checkbox state and a server-backed setter."""

    __slots__ = ("_id", "_text", "_status", "_update", "_pending")

    def __init__(
        self,
        ID: int,
        text: str,
        status: bool,
        update: Callable[[int, bool], Any],
    ) -> None:
        self._id = ID
        self._text = text
        self._status = status
        self._update = update
        self._pending: list[tuple[Future, bool]] = []

    @property
    def ID(self) -> int:
        """Stable server identifier for this step."""
        return self._id

    @property
    def text(self) -> str:
        """Step text as displayed in eLabFTW."""
        return self._text

    @property
    def status(self) -> bool:
        """Last confirmed checkbox state; reading it does not contact the server."""
        while self._pending and self._pending[0][0].done():
            future, value = self._pending.pop(0)
            if not future.cancelled() and future.exception() is None:
                self._status = value
        return self._status

    @status.setter
    def status(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError("Step status must be True or False")
        result = self._update(self.ID, value)
        if isinstance(result, Future):
            self._pending.append((result, value))
        else:
            self._status = value

    def __repr__(self) -> str:
        return f"Step(ID={self.ID}, text={self.text!r}, status={self.status})"
