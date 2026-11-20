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


def test_n6791a_power_rating_at_modelled_terminal_voltage_no_clipping() -> None:
    instrument = bench_sim()
    state = instrument.channels[2]
    assert state.rated_power_w == 100.0
    assert state.max_voltage == 60.0
    assert state.max_current == 20.0
    instrument.execute("SIM:LOAD:VOLT 10,(@2)")
    instrument.execute("SIM:LOAD:CURR 9.9,(@2)")
    instrument.execute("OUTP ON,(@2)")
    assert instrument.execute("MEAS:POW? (@2)") == "99.0"
    assert instrument.execute("SIM:LOAD:CURR? (@2)") == "9.9"
    instrument.execute("SIM:LOAD:CURR 10,(@2)")
    assert instrument.execute("MEAS:POW? (@2)") == "100.0"
    instrument.execute("SIM:LOAD:CURR 10.01,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))
    assert instrument.execute("SIM:LOAD:CURR? (@2)") == "10.0"
    assert instrument.execute("MEAS:POW? (@2)") == "100.0"


def test_n6791a_voltage_change_cannot_make_existing_load_over_power() -> None:
    instrument = bench_sim()
    instrument.execute("SIM:LOAD:CURR 20,(@2)")
    instrument.execute("SIM:LOAD:VOLT 5,(@2)")
    assert instrument.execute("SIM:LOAD:VOLT? (@2)") == "5.0"
    instrument.execute("SIM:LOAD:VOLT 5.01,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))
    assert instrument.execute("SIM:LOAD:VOLT? (@2)") == "5.0"
    instrument.execute("SIM:LOAD:POW 100.1,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))
    assert instrument.execute("SIM:LOAD:POW? (@2)") == "0.0"


def test_n6791a_rejects_nonfinite_and_oversized_list_without_mutation() -> None:
    instrument = bench_sim()
    instrument.execute("SIM:LOAD:VOLT 10,(@2)")
    before = instrument.channels[2].current_list
    instrument.execute("LIST:CURR 9.9,10.01,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))
    assert instrument.channels[2].current_list == before
    instrument.execute("SIM:LOAD:CURR INF,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))
    assert instrument.execute("SIM:LOAD:CURR? (@2)") == "0.0"
    instrument.execute("SIM:LOAD:VOLT 60.01,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))


def test_n6792a_has_distinct_200w_40a_rating() -> None:
    instrument = SimN6700Instrument(
        models={1: "N6775A", 2: "N6792A"}, mainframe_model="N6700C"
    )
    st = instrument.channels[2]
    assert (st.max_voltage, st.max_current, st.rated_power_w) == (60, 40, 200)
    instrument.execute("SIM:LOAD:VOLT 10,(@2)")
    instrument.execute("SIM:LOAD:CURR 20,(@2)")
    assert instrument.execute("SIM:LOAD:CURR? (@2)") == "20.0"
    instrument.execute("SIM:LOAD:CURR 20.01,(@2)")
    assert "-222" in str(instrument.execute("SYST:ERR?"))
    assert instrument.execute("SIM:LOAD:CURR? (@2)") == "20.0"
