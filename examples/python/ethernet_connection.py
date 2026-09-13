"""Connect over raw TCP/SCPI (normally port 5025) and read identity.

Run: python examples/python/ethernet_connection.py --host 192.168.1.50
"""

from __future__ import annotations

import argparse

from keysight_n6700 import N6700


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5025)
    args = parser.parse_args()

    with N6700.connect_ethernet(args.host, args.port) as instrument:
        print(instrument.get_identity())
        print(instrument.discover_modules())


if __name__ == "__main__":
    main()
