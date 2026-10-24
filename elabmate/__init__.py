"""Public API for elabmate.

@author Thibaut Jacqmin
"""

from typing import TYPE_CHECKING

from .async_bridge import AsyncElabBridge
from .async_experiment import AsyncElabExperiment
from .bridge import ElabBridge
from .client import ElabClient
from .experiment import ElabExperiment
from .resource import ElabResource
from .exceptions import (
    DeletedExperiment,
    DuplicateTitle,
    ElabException,
    InvalidCategory,
    InvalidID,
    InvalidStatus,
    InvalidTemplate,
    InvalidTitle,
)

__all__ = [
    "AcquisitionAnalysisManager",
    "AsyncElabBridge",
    "AsyncElabExperiment",
    "ElabBridge",
    "ElabClient",
    "ElabExperiment",
    "ElabResource",
    "DeletedExperiment",
    "DuplicateTitle",
    "ElabException",
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
                    "The eLabmate acquisition manager requires Labmate. Install your "
                    "local Labmate checkout in this Python environment using "
                    "pip install -e <path-to-labmate>.",
                    name="labmate",
                ) from exc
            raise

        globals()[name] = AcquisitionAnalysisManager
        return AcquisitionAnalysisManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
