"""Framework-independent Python driver for Keysight/Agilent N6700 modular power systems.

Fully usable from plain Python, a notebook, pytest, or a CLI, with no
automation framework installed. An optional Robot Framework adapter lives in
the separate ``keysight_n6700_robotframework_adapter`` package
(``adapters/robotframework/``); nothing here imports it.
"""

from .channel import BaseChannel, ElectronicLoadChannel, PowerSupplyChannel, SMUChannel
from .driver import N6700
from .exceptions import (
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverConfigurationError,
    DriverConnectionError,
    DriverError,
    DriverPreconditionError,
    DriverSafetyError,
    DriverStateError,
    DriverTimeoutError,
    DriverUnsafeOperationError,
    DriverUnsupportedOperationError,
    DriverValidationError,
)
from .module_capabilities import ChannelCapabilities, ModuleType, classify_module
from .simulator import SimN6700Instrument
from .types import (
    ArrayMeasurement,
    InstrumentIdentity,
    Measurement,
    PowerMeasurement,
    ProtectionClearResult,
    ProtectionStatus,
    RemoteState,
    ScpiErrorRecord,
    ShutdownResult,
)
from .version import __version__

__all__ = [
    "N6700",
    "ArrayMeasurement",
    "BaseChannel",
    "ChannelCapabilities",
    "DriverArgumentValueError",
    "DriverCommandRejectedError",
    "DriverConfigurationError",
    "DriverConnectionError",
    "DriverError",
    "DriverPreconditionError",
    "DriverSafetyError",
    "DriverStateError",
    "DriverTimeoutError",
    "DriverUnsafeOperationError",
    "DriverUnsupportedOperationError",
    "DriverValidationError",
    "ElectronicLoadChannel",
    "InstrumentIdentity",
    "Measurement",
    "ModuleType",
    "PowerMeasurement",
    "PowerSupplyChannel",
    "ProtectionClearResult",
    "ProtectionStatus",
    "RemoteState",
    "SMUChannel",
    "ScpiErrorRecord",
    "ShutdownResult",
    "SimN6700Instrument",
    "__version__",
    "classify_module",
]
