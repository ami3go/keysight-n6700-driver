"""LPDS-013 capability model: self-describing capability discovery.

Every public method that talks to the instrument is annotated with
:func:`capability`, which attaches a :class:`CapabilityRecord` to the
function object without changing how it is called. :class:`CapabilityDiscoveryMixin`
then implements the six LPDS-013 §7.1 discovery methods generically, by
scanning the concrete driver class for annotated methods — there is exactly
one place (the decorator on each method) where a capability is described, and
the discovery API, the AI contract (LPDS-017), and the LPDS-019 conformance
inventory are all generated from it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

RiskLevel = Literal["none", "low", "medium", "high", "critical"]
SupportState = Literal[
    "supported", "unsupported", "conditional", "restricted", "unavailable", "deprecated", "unknown"
]
SupportConfidence = Literal["declared", "configured", "detected", "confirmed", "assumed", "unknown"]

#: LPDS-013 §10.2 standard domains this driver's capabilities are drawn from.
STANDARD_DOMAINS = frozenset(
    {
        "connection",
        "identity",
        "configuration",
        "source",
        "measure",
        "load",
        "switch",
        "trigger",
        "acquisition",
        "waveform",
        "channel",
        "status",
        "system",
        "safety",
        "calibration",
        "file",
        "logging",
        "firmware",
        "simulation",
        "utility",
    }
)

_T = TypeVar("_T", bound=Callable[..., Any])


@dataclass(frozen=True)
class CapabilityRecord:
    """One LPDS-013 §11 capability record, reduced to the fields this driver
    can populate without a hardware-in-the-loop test bench.
    """

    capability_id: str
    display_name: str
    description: str
    category: str
    python_method: str
    risk_level: RiskLevel = "low"
    mutating: bool = False
    may_energize_or_sink_power: bool = False
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    support_state: SupportState = "supported"
    support_confidence: SupportConfidence = "declared"
    canonical_method: str | None = None
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "standard": self.category in STANDARD_DOMAINS or self.capability_id.startswith("vendor."),
            "risk_level": self.risk_level,
            "mutating": self.mutating,
            "may_energize_or_sink_power": self.may_energize_or_sink_power,
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "side_effects": list(self.side_effects),
            "support": {"state": self.support_state, "confidence": self.support_confidence},
            "binding": {
                "python_method": self.python_method,
                "canonical_method": self.canonical_method or self.python_method,
                "aliases": list(self.aliases),
            },
        }


def capability(
    capability_id: str,
    *,
    category: str,
    display_name: str | None = None,
    description: str | None = None,
    risk_level: RiskLevel = "low",
    mutating: bool = False,
    may_energize_or_sink_power: bool = False,
    preconditions: Iterable[str] = (),
    postconditions: Iterable[str] = (),
    side_effects: Iterable[str] = (),
    canonical_method: str | None = None,
    aliases: Iterable[str] = (),
) -> Callable[[_T], _T]:
    """Attach a :class:`CapabilityRecord` to a driver method. Does not wrap the call."""

    def decorator(func: _T) -> _T:
        record = CapabilityRecord(
            capability_id=capability_id,
            display_name=display_name or func.__name__.replace("_", " ").title(),
            description=description or (func.__doc__ or "").strip().split("\n", 1)[0],
            category=category,
            python_method=func.__name__,
            risk_level=risk_level,
            mutating=mutating,
            may_energize_or_sink_power=may_energize_or_sink_power,
            preconditions=tuple(preconditions),
            postconditions=tuple(postconditions),
            side_effects=tuple(side_effects),
            canonical_method=canonical_method,
            aliases=tuple(aliases),
        )
        func.__lpds_capability__ = record  # type: ignore[attr-defined]
        return func

    return decorator


def iter_capabilities(cls: type) -> Iterable[CapabilityRecord]:
    """Yield the :class:`CapabilityRecord` of every ``@capability``-annotated method on ``cls``."""
    seen: set[str] = set()
    for klass in cls.__mro__:
        for name, attr in vars(klass).items():
            record = getattr(attr, "__lpds_capability__", None)
            if isinstance(record, CapabilityRecord) and name not in seen:
                seen.add(name)
                yield record


@dataclass
class CapabilityQuery:
    category: str | None = None
    risk_level: RiskLevel | None = None
    mutating: bool | None = None
    support_state: SupportState | None = None


class CapabilityDiscoveryMixin:
    """The six LPDS-013 §7.1 mandatory discovery methods, implemented generically."""

    def get_capability_model(self) -> list[dict[str, Any]]:
        """Return every declared capability record as a plain dict."""
        return [record.as_dict() for record in iter_capabilities(type(self))]

    def get_driver_capability(self, capability_id: str) -> dict[str, Any] | None:
        """Return one capability record by ID, or ``None`` if not declared."""
        for record in iter_capabilities(type(self)):
            if record.capability_id == capability_id:
                return record.as_dict()
        return None

    def find_driver_capabilities(self, query: CapabilityQuery | None = None) -> list[dict[str, Any]]:
        """Return capability records matching ``query`` (all, if omitted)."""
        query = query or CapabilityQuery()
        results = []
        for record in iter_capabilities(type(self)):
            if query.category is not None and record.category != query.category:
                continue
            if query.risk_level is not None and record.risk_level != query.risk_level:
                continue
            if query.mutating is not None and record.mutating != query.mutating:
                continue
            if query.support_state is not None and record.support_state != query.support_state:
                continue
            results.append(record.as_dict())
        return results

    def get_driver_features(self) -> list[str]:
        """Return every declared capability ID (LPDS-002's flat feature list is
        ``get_driver_capabilities()``; this is LPDS-013's parallel, ID-based
        view over the same annotations).
        """
        return sorted(record.capability_id for record in iter_capabilities(type(self)))

    def refresh_driver_capabilities(self) -> list[dict[str, Any]]:
        """Re-scan the class for capability annotations.

        Capabilities here are static, class-level annotations, not runtime
        module discovery, so this returns the same set as
        :meth:`get_capability_model`; it exists so a caller that always
        refreshes before trusting capabilities has one call to make regardless
        of driver.
        """
        return self.get_capability_model()

    def validate_driver_capabilities(self) -> dict[str, Any]:
        """Check the declared capability set for internal consistency.

        Returns a dict with ``valid`` and a list of ``problems``, so a caller
        can decide whether to trust the model without this raising.
        """
        problems: list[str] = []
        seen_ids: set[str] = set()
        for record in iter_capabilities(type(self)):
            if record.capability_id in seen_ids:
                problems.append(f"duplicate capability_id: {record.capability_id}")
            seen_ids.add(record.capability_id)
            if not hasattr(self, record.python_method):
                problems.append(
                    f"capability {record.capability_id} references missing method {record.python_method}"
                )
            if "." not in record.capability_id:
                problems.append(f"capability_id {record.capability_id} is not dot-separated")
        return {"valid": not problems, "problems": problems}
