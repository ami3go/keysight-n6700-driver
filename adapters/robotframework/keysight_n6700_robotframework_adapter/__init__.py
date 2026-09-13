"""Robot Framework adapter for :mod:`keysight_n6700`.

A separate, thin translation layer per LPDS-015: this package imports the
driver's public API only, never the reverse.
"""

from .adapter import KeysightN6700Library
from .version import __version__

__all__ = ["KeysightN6700Library", "__version__"]
