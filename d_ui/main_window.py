import time
import math
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from c_ETP.ETP_utils import CHANNEL_COLORS, run_sequence

CONFIG_DIR = Path(__file__).resolve().parent.parent / "a_config"

STATE_COLORS = {
    "idle": "#52514e",
    "running": "#2a78d6",
    "stopping": "#eda100",
    "stopped": "#898781",
    "complete": "#1baf7a",
    "error": "#e34948",
    "collect": "#e87ba4",
}


def discover_configs(config_dir):
    config_dir = Path(config_dir)
    files = sorted(list(config_dir.glob("*.yaml")) + list(config_dir.glob("*.yml")))
    return files


def _format_mmss(seconds):
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class WellPanel:
    """The controls + status for one well: config picker, activate/stop, live readout."""

    def __init__(self, parent, idx, name, config_names, on_activate, on_stop, on_rename=None):
        self.idx = idx
        self.default_name = f"Well {idx + 1}"
        self.name = name
        self.on_rename = on_rename
        self.state = "idle"

        self.frame = ttk.LabelFrame(parent, text=name, padding=10)

        ttk.Button(self.frame, text="✎ Rename", width=10,
                   command=self._prompt_rename).pack(anchor="e", pady=(0, 6))

        self.config_var = tk.StringVar(value=config_names[0] if config_names else "")
        self.combo = ttk.Combobox(self.frame, textvariable=self.config_var, values=config_names,
                                   state="readonly" if config_names else "disabled", width=24)
        self.combo.pack(fill="x", pady=(0, 8))

        self.status_label = tk.Label(self.frame, text="Idle", font=("Segoe UI", 11, "bold"),
                                      fg=STATE_COLORS["idle"], anchor="w", wraplength=200, justify="left")
        self.status_label.pack(fill="x")

        self.readout_var = tk.StringVar(value="V: --.-")
        ttk.Label(self.frame, textvariable=self.readout_var, font=("Consolas", 9)).pack(anchor="w", pady=(2, 4))

        self.step_var = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self.step_var, font=("Consolas", 9)).pack(anchor="w", pady=(0, 10))

        btn_row = ttk.Frame(self.frame)
        btn_row.pack(fill="x")
        self.activate_btn = ttk.Button(btn_row, text="Activate", command=lambda: on_activate(idx))
        self.activate_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=lambda: on_stop(idx), state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x")

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def _prompt_rename(self):
        current = "" if self.name == self.default_name else self.name
        new = simpledialog.askstring(
            "Rename well",
            f"Experiment name for {self.default_name}\n"
            f"(leave blank to restore the default name):",
            initialvalue=current, parent=self.frame)
        if new is None:
            return
        self.set_name(new.strip() or self.default_name)

    def set_name(self, name):
        self.name = name
        self.frame.configure(text=name)
        if self.on_rename is not None:
            self.on_rename(self.idx, name)

    def set_config_options(self, config_names):
        self.combo.configure(values=config_names, state="readonly" if config_names else "disabled")
        if config_names and self.config_var.get() not in config_names:
            self.config_var.set(config_names[0])

    def set_state(self, state, detail=None):
        self.state = state
        if state == "running":
            self.status_label.config(text="Running", fg=STATE_COLORS["running"])
            self.activate_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.combo.config(state="disabled")
        elif state == "stopping":
            self.status_label.config(text="Stopping…", fg=STATE_COLORS["stopping"])
            self.stop_btn.config(state="disabled")
        elif state == "stopped":
            self.status_label.config(text="Stopped", fg=STATE_COLORS["stopped"])
            self._reset_controls()
        elif state == "complete":
            self.status_label.config(text="Complete", fg=STATE_COLORS["complete"])
            self._reset_controls()
        elif state == "error":
            self.status_label.config(text=f"Error: {detail}", fg=STATE_COLORS["error"])
            self._reset_controls()
        else:
            self.status_label.config(text="Idle", fg=STATE_COLORS["idle"])
            self._reset_controls()

    def _reset_controls(self):
        self.activate_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.combo.config(state="readonly" if self.combo["values"] else "disabled")

    def update_readout(self, voltage):
        self.readout_var.set(f"V: {voltage:6.1f} V")

    def update_step(self, step_name, remaining_s, duration_s):
        if step_name is None:
            self.step_var.set("")
            return
        self.step_var.set(f"Step: {step_name}  ({_format_mmss(remaining_s)} left of {_format_mmss(duration_s)})")

    def flag_collect(self):
        self.status_label.config(text="⚠ COLLECT NOW", fg=STATE_COLORS["collect"])

    def clear_collect_flag(self):
        if self.state == "running":
            self.status_label.config(text="Running", fg=STATE_COLORS["running"])


class App(tk.Tk):
    """Four-well ETP control window: per-well activate/stop, start-all/stop-all,
    a sequence-config picker sourced from a_config/, and a live voltage plot.
    """

    POLL_MS = 400

    def __init__(self, channels, well_names=None, eib=None, device_label="", config_dir=CONFIG_DIR):
        super().__init__()
        self.title("ETP 4-Well Control")
        self.geometry("1200x820")
        self.minsize(1000, 700)

        self.channels = channels
        self.n_wells = len(channels)
        self.well_names = well_names or [f"Well {i + 1}" for i in range(self.n_wells)]
        self.eib = eib
        self.config_dir = Path(config_dir)

        self.lock = threading.Lock()
        self.stop_events = {i: threading.Event() for i in range(self.n_wells)}
        self.collect_states = {}
        self.prev_collect = {i: False for i in range(self.n_wells)}
        self.sample_logs = {i: [] for i in range(self.n_wells)}
        self.step_states = {i: None for i in range(self.n_wells)}
        self.threads = {i: None for i in range(self.n_wells)}
        self.done_flags = {i: None for i in range(self.n_wells)}
        self.errors = {i: None for i in range(self.n_wells)}

        self.config_files = discover_configs(self.config_dir)
        self.config_names = [f.name for f in self.config_files]
        self.config_map = {f.name: str(f) for f in self.config_files}

        self._build_ui(device_label)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._tick)

    # --- layout ---

    def _build_ui(self, device_label):
        top = ttk.Frame(self, padding=(12, 10))
        top.pack(fill="x")
        ttk.Label(top, text="4-Well Epitachophoresis", font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(top, text=device_label, foreground="#52514e").pack(side="left", padx=(14, 0))

        btns = ttk.Frame(top)
        btns.pack(side="right")
        ttk.Button(btns, text="⟳ Refresh Configs", command=self._refresh_configs).pack(side="left", padx=4)
        ttk.Button(btns, text="Start All", command=self._start_all).pack(side="left", padx=4)
        ttk.Button(btns, text="Stop All", command=self._stop_all).pack(side="left", padx=4)
        self.diagnostic_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="Diagnostic Mode (Ω)", variable=self.diagnostic_var,
                         command=self._redraw_plot).pack(side="left", padx=(12, 4))

        wells_frame = ttk.Frame(self, padding=(12, 0))
        wells_frame.pack(fill="x")
        self.wells = []
        for i in range(self.n_wells):
            wells_frame.columnconfigure(i, weight=1, uniform="well")
            panel = WellPanel(wells_frame, i, self.well_names[i], self.config_names,
                               on_activate=self._activate, on_stop=self._stop,
                               on_rename=self._rename_well)
            panel.grid(row=0, column=i, sticky="nsew", padx=6, pady=8)
            self.wells.append(panel)

        if not self.config_names:
            messagebox.showwarning("No sequence configs found",
                                    f"No .yaml files were found in {self.config_dir}.")

        plot_frame = ttk.Frame(self, padding=(12, 6))
        plot_frame.pack(fill="both", expand=True)
        self.fig = Figure(figsize=(9, 4.5), facecolor="#fcfcfb")
        self.ax = self.fig.add_subplot(111, facecolor="#fcfcfb")
        self._style_voltage_axes()
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _style_voltage_axes(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor("#fcfcfb")
        ax.set_xlabel("Time (s)", color="#52514e")
        ax.set_ylabel("Voltage (V)", color="#52514e")
        ax.set_title("Live Well Voltage", color="#0b0b0b")
        ax.grid(True, color="#e1e0d9", linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#c3c2b7")
        ax.tick_params(colors="#898781")

    def _style_diagnostic_axes(self):
        ax = self.ax
        ax.clear()
        ax.set_facecolor("#fcfcfb")
        ax.set_xlabel("Time (s)", color="#52514e")
        ax.set_ylabel("Resistance (Ω)", color="#52514e")
        ax.set_title("Live Well Diagnostics — Resistance (V / I)", color="#0b0b0b")
        ax.grid(True, color="#e1e0d9", linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#c3c2b7")
        ax.tick_params(colors="#898781")

    def _refresh_configs(self):
        self.config_files = discover_configs(self.config_dir)
        self.config_names = [f.name for f in self.config_files]
        self.config_map = {f.name: str(f) for f in self.config_files}
        for w in self.wells:
            w.set_config_options(self.config_names)

    def _rename_well(self, idx, name):
        """Keep the plot legend / dialogs in sync with a user-renamed well."""
        self.well_names[idx] = name
        self._redraw_plot()

    # --- well lifecycle ---

    def _activate(self, idx):
        well = self.wells[idx]
        thread = self.threads[idx]
        if thread is not None and thread.is_alive():
            return
        name = well.config_var.get()
        if not name or name not in self.config_map:
            messagebox.showwarning("No config selected", f"Pick a sequence config for {well.name} first.")
            return
        yaml_path = self.config_map[name]

        stop_event = threading.Event()
        self.stop_events[idx] = stop_event
        self.collect_states[idx] = False
        self.prev_collect[idx] = False
        self.sample_logs[idx] = []
        self.step_states[idx] = None
        self.done_flags[idx] = None
        self.errors[idx] = None

        thread = threading.Thread(target=self._worker, args=(idx, yaml_path, stop_event), daemon=True)
        self.threads[idx] = thread
        well.set_state("running")
        thread.start()

    def _worker(self, idx, yaml_path, stop_event):
        channel = self.channels[idx]

        def on_sample(t, v, i, w):
            self.sample_logs[idx].append((t, v, i, w))

        def on_step_progress(step_name, elapsed, duration):
            self.step_states[idx] = (step_name, elapsed, duration)

        try:
            run_sequence(channel, yaml_path, lock=self.lock, collect_states=self.collect_states,
                         channel_key=idx, stop_event=stop_event, on_sample=on_sample,
                         on_step_progress=on_step_progress)
            self.done_flags[idx] = "stopped" if stop_event.is_set() else "complete"
        except Exception as exc:
            self.errors[idx] = str(exc)
            self.done_flags[idx] = "error"

    def _stop(self, idx):
        thread = self.threads[idx]
        if thread is not None and thread.is_alive():
            self.stop_events[idx].set()
            self.wells[idx].set_state("stopping")

    def _start_all(self):
        for idx in range(self.n_wells):
            self._activate(idx)

    def _stop_all(self):
        for idx in range(self.n_wells):
            self._stop(idx)

    # --- polling loop (GUI thread only touches Tk/matplotlib from here) ---

    def _tick(self):
        for idx in range(self.n_wells):
            well = self.wells[idx]
            thread = self.threads[idx]

            if thread is not None and not thread.is_alive() and well.state in ("running", "stopping"):
                result = self.done_flags[idx]
                if result == "error":
                    well.set_state("error", detail=self.errors[idx])
                elif result == "stopped":
                    well.set_state("stopped")
                else:
                    well.set_state("complete")
                self.threads[idx] = None
                self.step_states[idx] = None
                well.update_step(None, None, None)

            collecting = self.collect_states.get(idx, False)
            if collecting and not self.prev_collect[idx]:
                self._trigger_collect_alert(idx)
            elif not collecting and self.prev_collect[idx]:
                well.clear_collect_flag()
            self.prev_collect[idx] = collecting

            log = self.sample_logs[idx]
            if log and well.state in ("running", "stopping"):
                well.update_readout(log[-1][1])

            step_state = self.step_states[idx]
            if step_state and well.state in ("running", "stopping"):
                step_name, elapsed, duration = step_state
                well.update_step(step_name, duration - elapsed, duration)

        self._redraw_plot()
        self.after(self.POLL_MS, self._tick)

    def _trigger_collect_alert(self, idx):
        well = self.wells[idx]
        well.flag_collect()
        self.bell()

        top = tk.Toplevel(self)
        top.title("Collect Sample")
        top.attributes("-topmost", True)
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=20)
        frame.pack()
        ttk.Label(frame, text=f"⚠ {well.name}", font=("Segoe UI", 13, "bold"),
                  foreground=STATE_COLORS["collect"]).pack()
        ttk.Label(frame, text="Cutoff voltage reached — collect your sample now.",
                  font=("Segoe UI", 10), wraplength=280, justify="center").pack(pady=(6, 14))
        ttk.Button(frame, text="Acknowledge", command=top.destroy).pack()

    @staticmethod
    def _robust_ylim(values, lower_pct=5, upper_pct=95, pad_frac=0.15, floor=0.0):
        """Percentile-based y-limits: a rare out-of-distribution shock (a fault
        spike, a transient near-zero-current blowup) sits outside the central
        [lower_pct, upper_pct] band and gets excluded from the range instead of
        single-handedly rescaling the whole axis and flattening the normal trend.
        The point itself still plots and visibly runs off the top/bottom edge.
        """
        clean = [v for v in values if not math.isnan(v)]
        if len(clean) < 2:
            return None
        ordered = sorted(clean)
        n = len(ordered)
        lo = ordered[max(0, int(n * lower_pct / 100))]
        hi = ordered[min(n - 1, int(n * upper_pct / 100))]
        if hi <= lo:
            hi = lo + 1.0
        pad = (hi - lo) * pad_frac
        return (max(floor, lo - pad), hi + pad)

    def _redraw_plot(self):
        diagnostic = self.diagnostic_var.get()
        if diagnostic:
            self._style_diagnostic_axes()
        else:
            self._style_voltage_axes()

        any_data = False
        all_resistances = []
        for idx in range(self.n_wells):
            samples = self.sample_logs[idx]
            if not samples:
                continue
            any_data = True
            color = CHANNEL_COLORS[idx % len(CHANNEL_COLORS)]
            if diagnostic:
                times, voltages, currents, _watts = zip(*samples)
                # Below ~0.5mA, V/I is dominated by noise rather than the well's
                # actual resistance, so blank those points instead of plotting a spike.
                resistances = [v / (i / 1000.0) if i > 0.5 else float("nan")
                               for v, i in zip(voltages, currents)]
                all_resistances.extend(resistances)
                self.ax.plot(times, resistances, color=color, linewidth=1.8, label=self.well_names[idx])
            else:
                times, voltages, _currents, _watts = zip(*samples)
                self.ax.plot(times, voltages, color=color, linewidth=1.8, label=self.well_names[idx])

        if diagnostic:
            ylim = self._robust_ylim(all_resistances)
            if ylim is not None:
                self.ax.set_ylim(*ylim)

        if any_data:
            self.ax.legend(frameon=False, labelcolor="#0b0b0b", loc="upper left")
        self.canvas.draw_idle()

    def _on_close(self):
        running = [i for i in range(self.n_wells)
                   if self.threads[i] is not None and self.threads[i].is_alive()]
        if running:
            if not messagebox.askyesno("Wells running", "Stop all wells and quit?"):
                return
            self._stop_all()
            deadline = time.time() + 3
            while time.time() < deadline and any(
                self.threads[i] is not None and self.threads[i].is_alive() for i in range(self.n_wells)
            ):
                time.sleep(0.1)
        if self.eib is not None:
            try:
                self.eib.CloseConnection()
            except Exception:
                pass
        self.destroy()
