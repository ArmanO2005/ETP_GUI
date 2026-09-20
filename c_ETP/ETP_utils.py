import time
import yaml
import threading
from contextlib import nullcontext

import matplotlib.pyplot as plt


# Categorical palette (fixed order, CVD-validated) — one hue per channel line
CHANNEL_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def run_channels(channels, channel_indices, yaml_path, plot_path="voltage_plot.png", collect_states=None):
    """Run the same sequence config on a subset of channels concurrently.

    channels is the full list of channel objects (e.g. addresses 0-3);
    channel_indices selects which of those to run, e.g. [0, 2, 3].

    All channels share one physical EIB connection, so hardware calls are
    serialized with a lock while each channel's timing loop still runs on
    its own thread.

    collect_states is an optional dict keyed by channel index. Pass in your
    own dict (e.g. from a GUI) to poll it live from another thread while this
    call is still running; otherwise one is created and only returned once
    every channel has finished.

    Records each channel's voltage over time and saves a plot to plot_path.
    """
    lock = threading.Lock()
    voltage_logs = {}
    if collect_states is None:
        collect_states = {}
    for idx in channel_indices:
        collect_states.setdefault(idx, False)

    def _run_and_record(idx):
        voltage_logs[idx] = run_sequence(channels[idx], yaml_path, lock=lock, collect_states=collect_states, channel_key=idx)

    threads = [threading.Thread(target=_run_and_record, args=(i,)) for i in channel_indices]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    plot_channel_voltages(voltage_logs, channel_indices, plot_path)

    return voltage_logs, collect_states


def plot_channel_voltages(voltage_logs, channel_indices, plot_path="voltage_plot.png"):
    """Plot voltage vs. time for each channel and save the figure to plot_path."""
    fig, ax = plt.subplots(figsize=(9, 5), facecolor="#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    for n, idx in enumerate(channel_indices):
        samples = voltage_logs.get(idx, [])
        if not samples:
            continue
        times, voltages, _currents, _watts = zip(*samples)
        ax.plot(times, voltages, color=CHANNEL_COLORS[n % len(CHANNEL_COLORS)], linewidth=2, label=f"Channel {idx}")

    ax.set_xlabel("Time (s)", color="#52514e")
    ax.set_ylabel("Voltage (V)", color="#52514e")
    ax.set_title("Channel Voltage vs. Time", color="#0b0b0b")
    ax.grid(True, color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c3c2b7")
    ax.tick_params(colors="#898781")
    if len(channel_indices) > 1:
        ax.legend(frameon=False, labelcolor="#0b0b0b")

    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    return plot_path


def run_sequence(channel, yaml_path, lock=None, collect_states=None, channel_key=None,
                  stop_event=None, on_sample=None, on_step_progress=None):
    """Run every step in yaml_path on channel, in order.

    stop_event is an optional threading.Event; when set, the current step
    winds down early (output is stopped) and no further steps run.

    on_sample is an optional callback(elapsed_t, voltage, current_mA, watts)
    invoked once per reading, with elapsed_t measured from the start of the
    whole sequence (not just the current step) so callers can plot a
    continuous trace.

    on_step_progress is an optional callback(step_name, elapsed_in_step_s,
    duration_s) invoked once when a step starts and again on every reading
    within it, so callers can show which step is active and how much of it
    is left (duration_s - elapsed_in_step_s).
    """
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)

    samples = []
    elapsed = 0.0
    for step_name, step in config.items():
        if stop_event is not None and stop_event.is_set():
            break
        print(f"Running {step_name}...")
        duration_s = step["duration_seconds"]
        step_on_sample = (lambda t, v, i, w, _elapsed=elapsed: on_sample(_elapsed + t, v, i, w)) if on_sample else None
        step_on_progress = (lambda t, d, _name=step_name: on_step_progress(_name, t, d)) if on_step_progress else None
        if step_on_progress:
            step_on_progress(0.0, duration_s)
        if step.get("type") == "collection":
            step_samples = run_collection_stage(
                channel,
                watts=step["watts"],
                current_limit_mA=step["current_mA"],
                voltage_limit_V=step["voltage_V"],
                duration_s=duration_s,
                cut_off_voltage=step["cut_off_voltage"],
                cut_off_wattage=step["cut_off_wattage"],
                cut_off_time_s=step["cut_off_time_s"],
                polarity=step.get("polarity", "positive"),
                lock=lock,
                collect_states=collect_states,
                channel_key=channel_key,
                stop_event=stop_event,
                on_sample=step_on_sample,
                on_step_progress=step_on_progress,
            )
        else:
            step_samples = run_step(
                channel,
                watts=step["watts"],
                current_limit_mA=step["current_mA"],
                voltage_limit_V=step["voltage_V"],
                duration_s=duration_s,
                polarity=step.get("polarity", "positive"),
                lock=lock,
                stop_event=stop_event,
                on_sample=step_on_sample,
                on_step_progress=step_on_progress,
            )
        samples.extend((elapsed + t, v, i, w) for t, v, i, w in step_samples)
        elapsed += step_samples[-1][0] if step_samples else 0.0

    return samples


INTERLOCK_OPEN_BIT = 0x80  # CEP01.GetOutputState() bit indicating the power-supply interlock switch is open


def _check_interlock(channel):
    channel.CmdGetStatus()
    if channel.GetOutputState() & INTERLOCK_OPEN_BIT:
        raise RuntimeError("Power supply interlock is open — close the safety switch before applying power.")


def run_step(channel, watts, current_limit_mA, voltage_limit_V, duration_s, polarity="positive", lock=None,
             stop_event=None, on_sample=None, on_step_progress=None):
    ctx = lock if lock is not None else nullcontext()
    samples = []

    with ctx:
        _check_interlock(channel)
        channel.CmdSetPower(watts)
        channel.CmdSetCurrent(current_limit_mA)
        channel.CmdSetVoltage(voltage_limit_V)
        channel.CmdSetPositive() if polarity == "positive" else channel.CmdSetNegative()

    start = time.time()
    while time.time() - start < duration_s:
        if stop_event is not None and stop_event.is_set():
            break
        with ctx:
            channel.CmdGetStatus()
            if channel.GetOutputState() & INTERLOCK_OPEN_BIT:
                channel.CmdStop()
                raise RuntimeError("Power supply interlock opened mid-step — output stopped.")
            t = time.time() - start
            voltage = channel.GetVoltage()
            current = channel.GetCurrent()
            power = channel.GetPower()
            samples.append((t, voltage, current, power))
            if on_sample:
                on_sample(t, voltage, current, power)
            if on_step_progress:
                on_step_progress(t, duration_s)
            print(f"  t={t:5.1f}s  P={power:.2f}W  V={voltage:.1f}V  I={current:.3f}mA")
        time.sleep(0.5)

    with ctx:
        channel.CmdStop()

    return samples


def run_collection_stage(channel, watts, current_limit_mA, voltage_limit_V, duration_s,
                          cut_off_voltage, cut_off_wattage, cut_off_time_s,
                          polarity="positive", lock=None, collect_states=None, channel_key=None,
                          stop_event=None, on_sample=None, on_step_progress=None):
    """Run at `watts` until duration_s elapses or the voltage rises to/above
    cut_off_voltage. If the cutoff is hit, flag collect_states[channel_key]
    (when provided) so a caller can prompt the user to collect their sample,
    then run at cut_off_wattage for cut_off_time_s before clearing the flag.
    """
    ctx = lock if lock is not None else nullcontext()
    samples = []

    with ctx:
        _check_interlock(channel)
        channel.CmdSetPower(watts)
        channel.CmdSetCurrent(current_limit_mA)
        channel.CmdSetVoltage(voltage_limit_V)
        channel.CmdSetPositive() if polarity == "positive" else channel.CmdSetNegative()

    start = time.time()
    hit_cutoff = False
    while time.time() - start < duration_s:
        if stop_event is not None and stop_event.is_set():
            break
        with ctx:
            channel.CmdGetStatus()
            if channel.GetOutputState() & INTERLOCK_OPEN_BIT:
                channel.CmdStop()
                raise RuntimeError("Power supply interlock opened mid-step — output stopped.")
            t = time.time() - start
            voltage = channel.GetVoltage()
            current = channel.GetCurrent()
            power = channel.GetPower()
            samples.append((t, voltage, current, power))
            if on_sample:
                on_sample(t, voltage, current, power)
            if on_step_progress:
                on_step_progress(t, duration_s)
            print(f"  t={t:5.1f}s  P={power:.2f}W  V={voltage:.1f}V  I={current:.3f}mA")
            if voltage >= cut_off_voltage:
                hit_cutoff = True
        if hit_cutoff:
            break
        time.sleep(0.5)

    if not hit_cutoff:
        with ctx:
            channel.CmdStop()
        return samples

    print(f"  cutoff voltage reached ({voltage:.1f}V >= {cut_off_voltage}V) — collect now")
    if collect_states is not None:
        collect_states[channel_key] = True

    offset = samples[-1][0] if samples else 0.0
    cutoff_on_sample = (lambda t, v, i, w, _offset=offset: on_sample(_offset + t, v, i, w)) if on_sample else None
    if on_step_progress:
        on_step_progress(0.0, cut_off_time_s)
    cutoff_samples = run_step(
        channel,
        watts=cut_off_wattage,
        current_limit_mA=current_limit_mA,
        voltage_limit_V=voltage_limit_V,
        duration_s=cut_off_time_s,
        polarity=polarity,
        lock=lock,
        stop_event=stop_event,
        on_sample=cutoff_on_sample,
        on_step_progress=on_step_progress,
    )
    samples.extend((offset + t, v, i, w) for t, v, i, w in cutoff_samples)

    if collect_states is not None:
        collect_states[channel_key] = False

    return samples