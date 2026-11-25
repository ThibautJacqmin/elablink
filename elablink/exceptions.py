# -*- coding: utf-8 -*-
"""Custom exceptions for the eLabFTW helpers.

@author Thibaut Jacqmin
"""


class ELabError(Exception):
    """Base exception for eLabFTW integration."""

    pass


class DuplicateTitle(ELabError):
    """Raised when an experiment title already exists."""

    def __init__(self, title: str):
        super().__init__(f"An experiment with title '{title}' already exists.")


class AmbiguousTitle(ELabError):
    """Raised when several accessible records have the requested title."""

    def __init__(self, title: str, ids, kind: str = "experiment"):
        self.title = title
        self.ids = tuple(sorted(set(ids)))
        available = ", ".join(str(ID) for ID in self.ids)
        super().__init__(
            f"Several {kind}s have title {title!r} (IDs: {available}). "
            "Use ID=<number> to choose one explicitly."
        )


class InvalidTemplate(ELabError):
    """Raised when a template name cannot be resolved."""

    def __init__(self, name: str):
        super().__init__(f"Template named '{name}' not found.")


class InvalidTitle(ELabError):
    """Raised when a title lookup fails."""

    def __init__(self, title: str):
        super().__init__(f"{title} does not exist")


class InvalidID(ELabError):
    """Raised when an ID lookup fails."""

    def __init__(self, ID: str):
        super().__init__(f"{ID} does not exist")


def _choice_error(kind, name, available):
    message = f"{kind.capitalize()} '{name}' not found."
    if available is not None:
        choices = ", ".join(
            f"{title!r} (ID {ID})" for title, ID in available.items()
        ) or "none"
        plural = {"category": "categories", "status": "statuses", "tag": "tags"}[kind]
        message += f" Available {plural}: {choices}."
        if not available and kind in ("category", "status"):
            message += " This feature may be disabled, or no choices are configured for your account."
    return message


class InvalidCategory(ELabError):
    """Raised when a category lookup fails."""

    def __init__(self, name: str, available=None):
        super().__init__(_choice_error("category", name, available))


class InvalidStatus(ELabError):
    """Raised when a status lookup fails."""

    def __init__(self, name: str, available=None):
        super().__init__(_choice_error("status", name, available))


class InvalidTag(ELabError):
    """Raised when a tag is absent from the team's existing tags."""

    def __init__(self, name: str, available=None):
        super().__init__(_choice_error("tag", name, available))


class DeletedExperiment(ELabError):
    """Raised when an experiment is missing or deleted."""

    def __init__(self):
        super().__init__("Deleted experiment")


class OwnershipTransferError(ELabError):
    """Ownership could not be assigned or verified; the record still exists."""

    def __init__(self, entity_type: str, entity_id: int, message: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} #{entity_id}: {message}")


def safe_error_message(error: BaseException) -> str:
    """Describe an error without exposing API response headers or bodies."""
    if hasattr(error, "status") or (hasattr(error, "headers") and hasattr(error, "body")):
        status = getattr(error, "status", None)
        if isinstance(status, int) and status > 0:
            explanations = {
                401: "Check your API key.",
                403: "Check your access permissions.",
                404: "The requested record or attachment was not found.",
                408: "The request timed out.",
                413: "The file exceeds the server's upload limit.",
                429: "The server received too many requests; try again later.",
                503: "The server is temporarily unavailable.",
                504: "The request timed out.",
            }
            return f"The eLabFTW request failed (HTTP {status}). " + explanations.get(
                status, "Check the server and your connection."
            )
        return "The eLabFTW request failed. Check your connection and server configuration."
    message = str(error).strip()
    for marker in ("HTTP response headers:", "HTTP response body:"):
        message = message.split(marker, 1)[0].strip()
    return (message or type(error).__name__)[:800]
