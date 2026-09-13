# Installation

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"                 # driver + test tooling
pip install -e ".[dev,visa]"            # + PyVISA backend for real hardware
pip install -e ".[dev,robotframework]"  # + the Robot Framework adapter
```

`scpi-driver-core` is not yet published to PyPI; `pyproject.toml` pulls it
directly from its GitHub repository, pinned to a specific commit
(`tool.hatch.metadata.allow-direct-references = true` is required for this
and is already set).

Verify the install:

```bash
python -c "from keysight_n6700 import N6700; print(N6700.connect_simulated().get_identity())"
pytest
```
