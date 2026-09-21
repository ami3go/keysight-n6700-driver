"""Framework-independent Python driver for Keysight/Agilent N6700 modular power systems.

Fully usable from plain Python, a notebook, pytest, or a CLI, with no
automation framework installed. An optional Robot Framework adapter lives in
the separate ``keysight_n6700_robotframework_adapter`` package
(``adapters/robotframework/``); nothing here imports it.
"""

from .channel import BaseChannel, ElectronicLoadChannel, PowerSupplyChannel, SMUChannel
from .driver import N6700
from .exceptions import (
    DriverArgumentTypeError,
    DriverArgumentValueError,
    DriverCommandRejectedError,
    DriverConfigurationError,
    DriverConnectionError,
    DriverError,
    DriverMalformedResponseError,
    DriverOperationUncertainError,
    DriverPreconditionError,
    DriverRangeError,
    DriverReadError,
    DriverSafetyError,
    DriverStateError,
    DriverTimeoutError,
    DriverTransportError,
    DriverUnsafeOperationError,
    DriverUnsupportedOperationError,
    DriverUnsupportedValueError,
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
    QuesBit,
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
    "DriverArgumentTypeError",
    "DriverArgumentValueError",
    "DriverCommandRejectedError",
    "DriverConfigurationError",
    "DriverConnectionError",
    "DriverError",
    "DriverMalformedResponseError",
    "DriverOperationUncertainError",
    "DriverPreconditionError",
    "DriverRangeError",
    "DriverReadError",
    "DriverSafetyError",
    "DriverStateError",
    "DriverTimeoutError",
    "DriverTransportError",
    "DriverUnsafeOperationError",
    "DriverUnsupportedOperationError",
    "DriverUnsupportedValueError",
    "DriverValidationError",
    "ElectronicLoadChannel",
    "InstrumentIdentity",
    "Measurement",
    "ModuleType",
    "PowerMeasurement",
    "PowerSupplyChannel",
    "ProtectionClearResult",
    "ProtectionStatus",
    "QuesBit",
    "RemoteState",
    "SMUChannel",
    "ScpiErrorRecord",
    "ShutdownResult",
    "SimN6700Instrument",
    "__version__",
    "classify_module",
]
