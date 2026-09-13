*** Settings ***
Library    keysight_n6700_robotframework_adapter.KeysightN6700Library
Suite Teardown    Disconnect All N6700

*** Test Cases ***
Configure And Measure One Channel
    Connect To Simulated N6700
    ${identity}=    Get N6700 Identity
    Should Contain    ${identity}[manufacturer]    KEYSIGHT

    Set N6700 Voltage    1    5V
    Set N6700 Current Limit    1    500mA
    Turn On N6700 Output    1

    N6700 Voltage Should Be    1    5V    tolerance=0.01V
    Turn Off N6700 Output    1

Two Independent Sessions
    Connect To Simulated N6700    alias=bench_a
    Connect To Simulated N6700    alias=bench_b
    Set N6700 Voltage    1    3.3V    alias=bench_a
    Set N6700 Voltage    1    5V    alias=bench_b
    ${a}=    Get N6700 Voltage Setpoint    1    alias=bench_a
    ${b}=    Get N6700 Voltage Setpoint    1    alias=bench_b
    Should Be Equal As Numbers    ${a}    3.3
    Should Be Equal As Numbers    ${b}    5.0
