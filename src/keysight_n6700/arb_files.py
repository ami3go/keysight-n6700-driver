"""Host-side ARB data files in the Keysight N6705 front-panel CSV format.

The instrument imports/exports user-defined and constant-dwell ARB data only
from the front panel (N6705C User's Guide, *Importing and Exporting Arb
Data*); there is no SCPI import command.  These helpers read and write the
same file layout on the host so a profile prepared for the front panel can be
programmed with the driver, and vice versa:

* optional notes rows;
* a tag row ``%arbtype=arbuservolt|arbusercurr|arbcdvolt|arbcdcurr``;
* constant-dwell only: a second tag row ``%constantdwell=<seconds>``;
* a data header ``VALUE,TIME,TRIGGER`` (user-defined) or ``VALUE``
  (constant-dwell), followed by one data row per point.

Power and resistance ARB tags are N679xA electronic-load types and are not
accepted by these source-channel helpers.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .exceptions import DriverArgumentValueError

ArbFileKind = Literal["user_defined", "constant_dwell"]
ArbQuantity = Literal["voltage", "current"]

_TAGS: dict[str, tuple[ArbFileKind, ArbQuantity]] = {
    "arbuservolt": ("user_defined", "voltage"),
    "arbusercurr": ("user_defined", "current"),
    "arbcdvolt": ("constant_dwell", "voltage"),
    "arbcdcurr": ("constant_dwell", "current"),
}
_TAG_FOR = {value: key for key, value in _TAGS.items()}


@dataclass(frozen=True)
class ArbCsvProfile:
    """One ARB profile from a Keysight ARB CSV file.

    For ``constant_dwell`` profiles ``dwell_s`` holds the single shared dwell.
    """

    kind: ArbFileKind
    quantity: ArbQuantity
    levels: tuple[float, ...]
    dwell_s: tuple[float, ...]
    begin_step_triggers: tuple[bool, ...] = ()

    @property
    def duration_s(self) -> float:
        if self.kind == "constant_dwell":
            return self.dwell_s[0] * len(self.levels)
        return sum(self.dwell_s)


def _number(raw: str, path: Path, line: int) -> float:
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise DriverArgumentValueError(f"{path}:{line}: {raw!r} is not a number") from exc
    if not math.isfinite(value):
        raise DriverArgumentValueError(f"{path}:{line}: {raw!r} is not finite")
    return value


def read_arb_csv(path: str | Path) -> ArbCsvProfile:
    """Parse a Keysight user-defined or constant-dwell ARB CSV file."""
    source = Path(path)
    kind: ArbFileKind | None = None
    quantity: ArbQuantity | None = None
    constant_dwell: float | None = None
    header_seen = False
    levels: list[float] = []
    dwells: list[float] = []
    triggers: list[bool] = []
    with source.open(newline="", encoding="utf-8-sig") as handle:
        for line, row in enumerate(csv.reader(handle), start=1):
            cells = [cell.strip() for cell in row]
            while cells and not cells[-1]:
                cells.pop()
            if not cells:
                continue
            first = cells[0].lower().replace(" ", "")
            if first.startswith("%arbtype="):
                tag = first.split("=", 1)[1]
                if tag not in _TAGS:
                    raise DriverArgumentValueError(
                        f"{source}:{line}: unsupported ARB type {tag!r}; "
                        f"expected one of {sorted(_TAGS)}"
                    )
                kind, quantity = _TAGS[tag]
                continue
            if first.startswith("%constantdwell="):
                constant_dwell = _number(first.split("=", 1)[1], source, line)
                continue
            if kind is None:
                continue  # notes section
            if not header_seen:
                expected = ["value"] if kind == "constant_dwell" else ["value", "time", "trigger"]
                if [cell.lower() for cell in cells] != expected:
                    raise DriverArgumentValueError(
                        f"{source}:{line}: expected data header {','.join(expected).upper()}"
                    )
                header_seen = True
                continue
            if kind == "constant_dwell":
                if len(cells) != 1:
                    raise DriverArgumentValueError(f"{source}:{line}: expected one VALUE column")
                levels.append(_number(cells[0], source, line))
                continue
            if len(cells) != 3:
                raise DriverArgumentValueError(
                    f"{source}:{line}: expected VALUE,TIME,TRIGGER columns"
                )
            levels.append(_number(cells[0], source, line))
            dwells.append(_number(cells[1], source, line))
            trigger = _number(cells[2], source, line)
            if trigger not in {0.0, 1.0}:
                raise DriverArgumentValueError(f"{source}:{line}: TRIGGER must be 0 or 1")
            triggers.append(trigger == 1.0)
    if kind is None or quantity is None:
        raise DriverArgumentValueError(f"{source}: missing %arbtype tag row")
    if not levels:
        raise DriverArgumentValueError(f"{source}: no data rows")
    if kind == "constant_dwell":
        if constant_dwell is None:
            raise DriverArgumentValueError(f"{source}: missing %constantdwell tag row")
        dwells = [constant_dwell]
    return ArbCsvProfile(
        kind=kind,
        quantity=quantity,
        levels=tuple(levels),
        dwell_s=tuple(dwells),
        begin_step_triggers=tuple(triggers) if any(triggers) else (),
    )


def write_arb_csv(
    path: str | Path,
    *,
    kind: ArbFileKind,
    quantity: ArbQuantity,
    levels: Sequence[float],
    dwell_s: float | Sequence[float],
    begin_step_triggers: Sequence[bool] = (),
    notes: Sequence[str] = (),
) -> Path:
    """Write a profile in the Keysight ARB CSV layout and return the path."""
    if (kind, quantity) not in _TAG_FOR:
        raise DriverArgumentValueError("kind/quantity must be user_defined|constant_dwell and voltage|current")
    values = [float(value) for value in levels]
    if not values or any(not math.isfinite(value) for value in values):
        raise DriverArgumentValueError("levels must be a non-empty finite sequence")
    if isinstance(dwell_s, Sequence):
        dwells = [float(value) for value in dwell_s]
    else:
        dwells = [float(dwell_s)]
    if not dwells or any(not math.isfinite(value) or value < 0 for value in dwells):
        raise DriverArgumentValueError("dwell_s must be finite and >= 0")
    flags = [bool(flag) for flag in begin_step_triggers]
    if kind == "constant_dwell":
        if len(dwells) != 1:
            raise DriverArgumentValueError("a constant-dwell profile has exactly one dwell value")
    else:
        if len(dwells) == 1:
            dwells = dwells * len(values)
        if len(dwells) != len(values):
            raise DriverArgumentValueError("dwell_s must contain one value or one value per point")
        flags = flags or [False] * len(values)
        if len(flags) != len(values):
            raise DriverArgumentValueError("begin_step_triggers must match the number of points")
    target = Path(path)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for note in notes:
            writer.writerow([note])
        writer.writerow([f"%arbtype={_TAG_FOR[(kind, quantity)]}"])
        if kind == "constant_dwell":
            writer.writerow([f"%constantdwell={dwells[0]!r}"])
            writer.writerow(["VALUE"])
            for value in values:
                writer.writerow([repr(value)])
        else:
            writer.writerow(["VALUE", "TIME", "TRIGGER"])
            for value, dwell, flag in zip(values, dwells, flags, strict=True):
                writer.writerow([repr(value), repr(dwell), 1 if flag else 0])
    return target
