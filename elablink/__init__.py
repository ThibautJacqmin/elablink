"""Public API for elablink.

@author Thibaut Jacqmin
"""

from typing import TYPE_CHECKING

from .async_bridge import AsyncBridge
from .async_experiment import AsyncExperiment
from .bridge import Bridge
from .client import Client
from .experiment import Experiment
from .resource import Resource
from .exceptions import (
    DeletedExperiment,
    DuplicateTitle,
    ELabError,
    InvalidCategory,
    InvalidID,
    InvalidStatus,
    InvalidTemplate,
    InvalidTitle,
)

__all__ = [
    "AcquisitionAnalysisManager",
    "AsyncBridge",
    "AsyncExperiment",
    "Bridge",
    "Client",
    "Experiment",
    "Resource",
    "DeletedExperiment",
    "DuplicateTitle",
    "ELabError",
    "InvalidCategory",
    "InvalidID",
    "InvalidStatus",
    "InvalidTemplate",
    "InvalidTitle",
]

__version__ = "0.1.0"


if TYPE_CHECKING:
    from .acquisition_analysis_manager import AcquisitionAnalysisManager


def __getattr__(name):
    # Keep Labmate optional for users who only need the eLabFTW client.
    if name == "AcquisitionAnalysisManager":
        try:
            from .acquisition_analysis_manager import AcquisitionAnalysisManager
        except ModuleNotFoundError as exc:
            if exc.name == "labmate":
                raise ModuleNotFoundError(
                    "The eLabLink acquisition manager requires Labmate. Install your "
                    "local Labmate checkout in this Python environment using "
                    "pip install -e <path-to-labmate>.",
                    name="labmate",
                ) from exc
            raise

        globals()[name] = AcquisitionAnalysisManager
        return AcquisitionAnalysisManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
