"""Host-side Keysight ARB CSV files."""

from __future__ import annotations

from pathlib import Path

import pytest

from keysight_n6700 import DriverArgumentValueError, read_arb_csv, write_arb_csv


def test_user_defined_round_trip_with_notes_and_triggers(tmp_path: Path) -> None:
    path = write_arb_csv(
        tmp_path / "dip.csv",
        kind="user_defined",
        quantity="voltage",
        levels=[12.0, 6.0, 12.0],
        dwell_s=[0.01, 0.02, 0.01],
        begin_step_triggers=[False, True, False],
        notes=["12 V dip, cold crank", "second note"],
    )
    profile = read_arb_csv(path)
    assert profile.kind == "user_defined"
    assert profile.quantity == "voltage"
    assert profile.levels == (12.0, 6.0, 12.0)
    assert profile.dwell_s == (0.01, 0.02, 0.01)
    assert profile.begin_step_triggers == (False, True, False)
    assert profile.duration_s == pytest.approx(0.04)


def test_constant_dwell_round_trip(tmp_path: Path) -> None:
    path = write_arb_csv(
        tmp_path / "cd.csv", kind="constant_dwell", quantity="current", levels=[0.1, 0.2],
        dwell_s=0.001,
    )
    profile = read_arb_csv(path)
    assert (profile.kind, profile.quantity) == ("constant_dwell", "current")
    assert profile.dwell_s == (0.001,)
    assert profile.duration_s == pytest.approx(0.002)


def test_reads_front_panel_layout_with_trailing_commas(tmp_path: Path) -> None:
    path = tmp_path / "panel.csv"
    path.write_text(
        "﻿Exported from N6705C,,\n%ArbType = ArbUserVolt,,\nValue,Time,Trigger\n"
        "1.5,0.1,0\n2.5,0.2,1\n",
        encoding="utf-8",
    )
    profile = read_arb_csv(path)
    assert profile.levels == (1.5, 2.5)
    assert profile.begin_step_triggers == (False, True)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("VALUE,TIME,TRIGGER\n1,0.1,0\n", "missing %arbtype"),
        ("%arbtype=arbuserpower\nVALUE\n1\n", "unsupported ARB type"),
        ("%arbtype=arbuservolt\nVALUE\n1\n", "expected data header"),
        ("%arbtype=arbuservolt\nVALUE,TIME,TRIGGER\n1,0.1,2\n", "TRIGGER must be 0 or 1"),
        ("%arbtype=arbuservolt\nVALUE,TIME,TRIGGER\n1,abc,0\n", "not a number"),
        ("%arbtype=arbuservolt\nVALUE,TIME,TRIGGER\n1,0.1\n", "VALUE,TIME,TRIGGER"),
        ("%arbtype=arbuservolt\nVALUE,TIME,TRIGGER\n", "no data rows"),
        ("%arbtype=arbcdvolt\nVALUE\n1\n", "missing %constantdwell"),
        ("%arbtype=arbcdvolt\n%constantdwell=nan\nVALUE\n1\n", "not finite"),
    ],
)
def test_malformed_files_are_rejected(tmp_path: Path, content: str, match: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(DriverArgumentValueError, match=match):
        read_arb_csv(path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "user_defined", "quantity": "power", "levels": [1.0], "dwell_s": 0.1},
        {"kind": "user_defined", "quantity": "voltage", "levels": [], "dwell_s": 0.1},
        {"kind": "user_defined", "quantity": "voltage", "levels": [1.0], "dwell_s": -0.1},
        {"kind": "user_defined", "quantity": "voltage", "levels": [1.0, 2.0], "dwell_s": [0.1] * 3},
        {
            "kind": "user_defined", "quantity": "voltage", "levels": [1.0, 2.0], "dwell_s": 0.1,
            "begin_step_triggers": [True],
        },
        {"kind": "constant_dwell", "quantity": "voltage", "levels": [1.0], "dwell_s": [0.1, 0.2]},
    ],
)
def test_write_rejects_inconsistent_profiles(tmp_path: Path, kwargs) -> None:
    with pytest.raises(DriverArgumentValueError):
        write_arb_csv(tmp_path / "out.csv", **kwargs)
