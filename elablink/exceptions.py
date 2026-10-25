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


class InvalidCategory(ELabError):
    """Raised when a category lookup fails."""

    def __init__(self, name: str):
        super().__init__(f"Category '{name}' not found.")


class InvalidStatus(ELabError):
    """Raised when a status lookup fails."""

    def __init__(self, name: str):
        super().__init__(f"Status '{name}' not found.")


class DeletedExperiment(ELabError):
    """Raised when an experiment is missing or deleted."""

    def __init__(self):
        super().__init__("Deleted experiment")
