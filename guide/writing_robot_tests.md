# Writing Robot Framework tests

```robotframework
*** Settings ***
Library    keysight_n6700_robotframework_adapter.KeysightN6700Library    auto_shutdown=True    strict_errors=True
Suite Teardown    Disconnect All N6700

*** Test Cases ***
Configure One Channel
    Connect To Simulated N6700
    Set N6700 Voltage    1    5V
    Set N6700 Current Limit    1    500mA
    Turn On N6700 Output    1
    N6700 Voltage Should Be    1    5V    tolerance=0.01V
    Turn Off N6700 Output    1
```

- Values accept engineering notation (`"5V"`, `"250mA"`, `"2.2k"`).
- Every keyword accepts an optional `alias=` argument to address a specific
  session — see `Connect To N6700` / `Select N6700` / `Get Connected N6700
  Aliases`.
- `auto_shutdown=True` (default) makes `Disconnect N6700` / `Disconnect All
  N6700` / suite teardown attempt a best-effort shutdown first.
- `strict_errors=True` (default) makes mutating keywords check the SCPI
  error queue afterward and fail the keyword if the instrument reported one.
- The adapter has no device logic: every keyword is a thin translation of a
  `keysight_n6700.N6700` public method — see
  [`adapters/robotframework/keysight_n6700_robotframework_adapter/adapter.py`](../adapters/robotframework/keysight_n6700_robotframework_adapter/adapter.py)
  for the exact mapping, or call the driver directly from a Python keyword
  library if you need something not yet exposed as a keyword.

More: [`examples/robot/`](../examples/robot/).
