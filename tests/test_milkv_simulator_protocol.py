"""PST-MILKV-SIM-001: protocol-only regression, no physical equipment.

These tests exercise the simulator directly; they do not establish fidelity to
a hardware trace.  Hardware-specific behavior requires independently archived
read-only N6700C traces before raising the fidelity classification.
"""
from __future__ import annotations

from keysight_n6700.simulator import SimN6700Instrument


def bench_sim() -> SimN6700Instrument:
    return SimN6700Instrument(
        models={1: "N6775A", 2: "N6791A"},
        mainframe_model="N6700C",
    )


def test_explicit_n6700c_inventory_keeps_both_module_slots() -> None:
    instrument = bench_sim()
    assert ",N6700C," in instrument.execute("*IDN?")
    assert instrument.execute("SYST:CHAN:COUN?") == "2"
    assert instrument.execute("SYST:CHAN:MOD? (@1:2)") == "N6775A,N6791A"
    assert instrument.execute("SYST:CHAN:MOD? (@2)") == "N6791A"
    assert instrument.execute("SYST:CHAN:OPT? (@1:2)") == "0,0"
    assert instrument.execute("SYST:CHAN:SER? (@1:2)") == "SIM0001,SIM0002"
    assert instrument.channels[1].max_voltage == 60.0
    assert instrument.channels[1].voltage_range == 60.0


def test_legacy_default_topology_is_not_replaced() -> None:
    instrument = SimN6700Instrument()
    assert ",N6705C," in instrument.execute("*IDN?")
    assert instrument.execute("SYST:CHAN:COUN?") == "4"
    assert instrument.channels[1].model == "N6751A"


def test_multiple_query_replies_preserved_in_program_order() -> None:
    instrument = bench_sim()
    response = instrument.execute(
        "OUTP? (@1);:VOLT? (@1);:STAT:QUES:COND? (@1);"
        ":STAT:QUES:COND? (@2);:SYST:ERR?"
    )
    assert response == '0;0.0;0;0;0,"No error"'


def test_source_program_message_write_commits_after_same_message_queries() -> None:
    instrument = bench_sim()
    response = instrument.execute('VOLT 12,(@1);:VOLT? (@1);:SYST:ERR?')
    assert response == '0.0;0,"No error"'
    assert instrument.execute('VOLT? (@1)') == '12.0'
    assert instrument.execute('SYST:ERR?') == '0,"No error"'


def test_terminated_write_then_query_reads_new_setpoint() -> None:
    instrument = bench_sim()
    assert instrument.execute("VOLT 12,(@1)") is None
    assert instrument.execute("VOLT? (@1)") == "12.0"


def test_unknown_query_error_remains_in_order() -> None:
    instrument = bench_sim()
    # Unsupported header returns an empty reply and pushes the error.
    # A following SYST:ERR? in the same message drains exactly that error.
    instrument = bench_sim()
    response = instrument.execute("BOGUS?;:SYST:ERR?")
    assert response.startswith(";")
    assert "-113" in response
    assert instrument.execute("SYST:ERR?") == '0,"No error"'


def test_compound_binary_data_cannot_corrupt_ascii_reply(monkeypatch) -> None:
    instrument = bench_sim()

    def unit(command: str) -> str | bytes | None:
        return b"#12ab" if command == "*IDN?" else "1"

    monkeypatch.setattr(instrument, "_execute_one", unit)
    assert instrument.execute("*IDN?") == b"#12ab"
    assert instrument.execute("*IDN?;*OPC?") == ""
    assert instrument.errors[-1][0] == -221
