"""Very basic Tk GUI for the power supply's waveform generator (ARB).

Pick a shape, edit its parameters and press Run. "Simulator" needs no
hardware; "Ethernet" drives a real N6705 at the given IP address.

Run: python examples/python/generator/gui.py
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from _play import play

from keysight_n6700 import N6700, ArbPulse, ArbRamp, ArbSine, ArbStaircase, ArbTrapezoid
from keysight_n6700.exceptions import DriverValidationError

# shape name -> (class, [(parameter, label, default), ...])
SHAPES = {
    "Pulse": (ArbPulse, [
        ("start_level", "Base level (V)", 12.0),
        ("top_level", "Pulse level (V)", 6.0),
        ("start_time_s", "Delay (s)", 0.01),
        ("top_time_s", "Pulse width (s)", 0.02),
        ("end_time_s", "Recovery (s)", 0.01),
    ]),
    "Ramp": (ArbRamp, [
        ("start_level", "Start (V)", 0.0),
        ("end_level", "End (V)", 10.0),
        ("start_time_s", "Delay (s)", 0.0),
        ("rise_time_s", "Ramp time (s)", 1.0),
        ("end_time_s", "Hold (s)", 0.5),
    ]),
    "Sine": (ArbSine, [
        ("offset", "Offset (V)", 5.0),
        ("amplitude", "Amplitude, peak (V)", 1.0),
        ("frequency_hz", "Frequency (Hz)", 50.0),
    ]),
    "Staircase": (ArbStaircase, [
        ("start_level", "Start (V)", 1.0),
        ("end_level", "End (V)", 5.0),
        ("steps", "Steps", 5),
        ("start_time_s", "Delay (s)", 0.0),
        ("staircase_time_s", "Staircase time (s)", 2.0),
        ("end_time_s", "Hold (s)", 0.0),
    ]),
    "Trapezoid": (ArbTrapezoid, [
        ("start_level", "Base level (V)", 2.0),
        ("top_level", "Top level (V)", 8.0),
        ("start_time_s", "Delay (s)", 0.0),
        ("rise_time_s", "Rise time (s)", 0.1),
        ("top_time_s", "Top time (s)", 0.3),
        ("fall_time_s", "Fall time (s)", 0.1),
        ("end_time_s", "Hold (s)", 0.0),
    ]),
}


def build_shape(name: str, values: dict[str, str]):
    """Create the ARB shape object from the text typed into the entry boxes."""
    shape_class, parameters = SHAPES[name]
    kwargs = {}
    for key, _label, default in parameters:
        text = values[key].strip()
        kwargs[key] = int(text) if isinstance(default, int) else float(text)
    return shape_class(**kwargs)


def run_waveform(
    shape, *, address: str | None, channel: int, current_limit_a: float,
    maximum_voltage_v: float, repeat_count: int,
) -> None:
    """Connect, configure the shape, play it once and disconnect."""
    connect = N6700.connect_simulated if address is None else (
        lambda: N6700.connect_ethernet(address)
    )
    with connect() as supply:
        supply.set_dc_current(current_limit_a, channel)
        supply.configure_voltage_arb_waveform(
            channel, shape,
            maximum_voltage_v=maximum_voltage_v,
            repeat_count=repeat_count,
        )
        play(supply, channel)


class GeneratorGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("N6705 waveform generator")
        frame = ttk.Frame(root, padding=10)
        frame.grid()

        # Connection
        self.connection = tk.StringVar(value="Simulator")
        self.address = tk.StringVar(value="192.168.0.6")
        ttk.Label(frame, text="Connection").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.connection, state="readonly",
                     values=["Simulator", "Ethernet"], width=12).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text="IP address").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.address, width=15).grid(row=1, column=1, sticky="w")

        # Common settings
        self.channel = self._entry(frame, 2, "Channel", "1")
        self.current_limit = self._entry(frame, 3, "Current limit (A)", "1.0")
        self.maximum_voltage = self._entry(frame, 4, "Max voltage (V)", "15.0")
        self.repeat_count = self._entry(frame, 5, "Repeat count", "1")

        # Shape selector + its parameter boxes
        self.shape = tk.StringVar(value="Pulse")
        ttk.Label(frame, text="Shape").grid(row=6, column=0, sticky="w", pady=(10, 0))
        selector = ttk.Combobox(frame, textvariable=self.shape, state="readonly",
                                values=list(SHAPES), width=12)
        selector.grid(row=6, column=1, sticky="w", pady=(10, 0))
        selector.bind("<<ComboboxSelected>>", lambda _event: self._show_parameters())
        self.parameter_frame = ttk.Frame(frame)
        self.parameter_frame.grid(row=7, column=0, columnspan=2, sticky="w")
        self.parameters: dict[str, tk.StringVar] = {}
        self._show_parameters()

        # Run + status
        self.run_button = ttk.Button(frame, text="Run", command=self._run)
        self.run_button.grid(row=8, column=0, columnspan=2, pady=10)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(frame, textvariable=self.status).grid(row=9, column=0, columnspan=2)

    @staticmethod
    def _entry(parent: ttk.Frame, row: int, label: str, default: str) -> tk.StringVar:
        variable = tk.StringVar(value=default)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=15).grid(row=row, column=1, sticky="w")
        return variable

    def _show_parameters(self) -> None:
        for widget in self.parameter_frame.winfo_children():
            widget.destroy()
        self.parameters = {}
        _, parameters = SHAPES[self.shape.get()]
        for row, (key, label, default) in enumerate(parameters):
            self.parameters[key] = self._entry(self.parameter_frame, row, label, str(default))

    def _run(self) -> None:
        try:
            shape = build_shape(self.shape.get(), {k: v.get() for k, v in self.parameters.items()})
            simulated = self.connection.get() == "Simulator"
            settings = {
                "address": None if simulated else self.address.get().strip(),
                "channel": int(self.channel.get()),
                "current_limit_a": float(self.current_limit.get()),
                "maximum_voltage_v": float(self.maximum_voltage.get()),
                "repeat_count": int(self.repeat_count.get()),
            }
        except (ValueError, DriverValidationError) as error:
            messagebox.showerror("Invalid input", str(error))
            return

        if settings["address"] is not None and not messagebox.askyesno(
            "Real hardware",
            f"Enable the output of channel {settings['channel']} at {settings['address']}?",
        ):
            return

        self.run_button.state(["disabled"])
        self.status.set("Running...")
        # Run in a background thread so the window stays responsive.
        threading.Thread(target=self._worker, args=(shape, settings), daemon=True).start()

    def _worker(self, shape, settings) -> None:
        try:
            run_waveform(shape, **settings)
            message = "Done"
        except Exception as error:  # show any driver error in the window
            message = f"Error: {error}"
        self.root.after(0, self._finished, message)

    def _finished(self, message: str) -> None:
        self.status.set(message)
        self.run_button.state(["!disabled"])


if __name__ == "__main__":
    root = tk.Tk()
    GeneratorGui(root)
    root.mainloop()
