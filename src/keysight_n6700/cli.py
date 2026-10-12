"""n6700ctl command-line utility."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .driver import N6700

READ_ONLY = {"idn", "discover", "measure", "errors", "capabilities"}


def _channels(text: str) -> list[int]:
    if text.lower() == "all":
        return [1, 2, 3, 4]
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def _open(args: argparse.Namespace) -> N6700:
    if args.sim:
        return N6700.connect_simulated()
    if args.ethernet is not None:
        return N6700.connect_ethernet(args.ethernet, port=args.port)
    if args.resource is None:
        raise SystemExit("--resource, --ethernet, or --sim is required")
    return N6700.connect_visa(args.resource)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="n6700ctl")
    parser.add_argument("--sim", action="store_true", help="Use built-in simulator")
    parser.add_argument("--resource", help="VISA resource string")
    parser.add_argument("--ethernet", metavar="HOST", help="Connect by raw SCPI TCP socket")
    parser.add_argument("--port", type=int, default=5025, help="SCPI TCP port (default: 5025)")
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

    exit_code = 0
    with _open(args) as inst:
        # Read-only monitoring must never change DUT power state merely because
        # the CLI session closes.
        if args.cmd in READ_ONLY:
            inst.set_auto_shutdown_on_disconnect(False)

        if args.cmd == "idn":
            print(inst.idn())
        elif args.cmd == "discover":
            modules = inst.discover_modules()
            data = {channel: caps.__dict__ for channel, caps in modules.items()}
            print(json.dumps(data, indent=2) if args.json else data)
        elif args.cmd == "measure":
            channels = [channel for channel in _channels(args.channels) if channel in inst.channels]
            data = {channel: inst.channel(channel).measure().__dict__ for channel in channels}
            print(json.dumps(data, indent=2, default=str) if args.json else data)
        elif args.cmd == "output-off":
            channels = [channel for channel in _channels(args.channels) if channel in inst.channels]
            for channel in channels:
                try:
                    inst.set_channel_enabled([channel], False)
                except Exception as exc:  # CLI boundary: report each channel and keep going.
                    print(f"channel {channel}: {exc}", file=sys.stderr)
                    exit_code = 2
        elif args.cmd == "shutdown-all":
            result = inst.shutdown_all()
            print(result)
            exit_code = 0 if result.success else 2
            # Avoid executing the same shutdown a second time during context cleanup.
            inst.set_auto_shutdown_on_disconnect(False)
        elif args.cmd == "errors":
            print(inst.drain_errors())
        elif args.cmd == "capabilities":
            capabilities = inst.get_capability_model()
            print(json.dumps(capabilities, indent=2) if args.json else capabilities)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
