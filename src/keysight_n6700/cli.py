"""n6700ctl command-line utility.

A CLI is itself an LPDS-015 "adapter" in spirit — it translates the driver's
public API to a command line — but is thin enough, and useful enough during
development, to ship inside the driver package rather than as a separate
distribution.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .driver import N6700


def _channels(text: str) -> list[int]:
    if text.lower() == "all":
        return [1, 2, 3, 4]
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def _open(args: argparse.Namespace) -> N6700:
    if args.sim:
        return N6700.connect_simulated()
    if args.resource is None:
        raise SystemExit("--resource is required unless --sim is used")
    return N6700.connect_visa(args.resource)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="n6700ctl")
    parser.add_argument("--sim", action="store_true", help="Use built-in simulator")
    parser.add_argument("--resource", help="VISA resource string")
    parser.add_argument("--json", action="store_true", help="Output JSON where supported")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("idn")
    sub.add_parser("discover")
    measure = sub.add_parser("measure")
    measure.add_argument("--channels", default="all")
    off = sub.add_parser("output-off")
    off.add_argument("--channels", default="all")
    sub.add_parser("shutdown-all")
    sub.add_parser("errors")
    sub.add_parser("capabilities")
    args = parser.parse_args(argv)
    with _open(args) as inst:
        if args.cmd == "idn":
            print(inst.idn())
        elif args.cmd == "discover":
            mods = inst.discover_modules()
            data = {ch: caps.__dict__ for ch, caps in mods.items()}
            print(json.dumps(data, indent=2) if args.json else data)
        elif args.cmd == "measure":
            chs = [ch for ch in _channels(args.channels) if ch in inst.channels]
            data = {ch: inst.channel(ch).measure().__dict__ for ch in chs}
            print(json.dumps(data, indent=2, default=str) if args.json else data)
        elif args.cmd == "output-off":
            chs = [ch for ch in _channels(args.channels) if ch in inst.channels]
            for ch in chs:
                try:
                    inst.set_channel_enabled([ch], False)
                except Exception as exc:
                    print(f"channel {ch}: {exc}", file=sys.stderr)
        elif args.cmd == "shutdown-all":
            print(inst.shutdown_all())
        elif args.cmd == "errors":
            print(inst.drain_errors())
        elif args.cmd == "capabilities":
            capabilities = inst.get_capability_model()
            print(json.dumps(capabilities, indent=2) if args.json else capabilities)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
