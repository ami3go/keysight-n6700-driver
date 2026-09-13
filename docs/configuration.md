# Configuration (LPDS-014)

```python
from keysight_n6700 import N6700

driver = N6700()
schema = driver.get_driver_configuration_schema()   # JSON Schema, Draft 2020-12
default = driver.get_driver_default_configuration()
driver.validate_driver_configuration(default)        # {"valid": True, "errors": []}
driver.import_driver_configuration(default, apply=True)
driver.export_driver_configuration("effective.json")
driver.save_driver_configuration("bench_a")           # config/profiles/bench_a.json
driver.load_driver_configuration("bench_a", apply=True)
driver.reset_driver_configuration()
```

The schema is mirrored at [`config/schema.json`](../config/schema.json); a
default document lives at [`config/default.json`](../config/default.json),
and an annotated example at [`config/example.json`](../config/example.json).

## Precedence

Package default → config file → constructor arguments → explicit call-time
arguments. There are no approved environment-variable overrides yet (LPDS-014
§9's third tier); that tier is skipped rather than faked until a real need
for one is documented.

## Fields

| Path | Meaning |
|---|---|
| `settings.transport.connection_type` | `visa` / `usb` / `ethernet` / `socket` / `simulated` |
| `settings.transport.port` | raw-TCP port, default 5025 |
| `settings.timeouts.communication_timeout_s` | default session timeout |
| `settings.safety.auto_shutdown_on_disconnect` | best-effort shutdown before disconnect |
| `settings.safety.strict_error_checking` | reserved for a future release |
| `settings.device.supported_manufacturers` | accepted `*IDN?` manufacturer strings |
| `settings.device.discover_modules_on_connect` | run `discover_modules()` automatically |

`settings.logging.audit_log_path` is annotated `x-lpds-sensitive: false` since
it is a path, not a secret; nothing in this schema currently carries a secret.
