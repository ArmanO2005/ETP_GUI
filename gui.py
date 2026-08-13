import argparse
import sys
import tkinter as tk
from tkinter import messagebox

import matplotlib
matplotlib.use("TkAgg")

from b_connection.port_utils import find_port_by_pid
from d_ui.main_window import App

ETP_DEVICE_PID = 60000
WELL_ADDRESSES = [1, 2, 3, 4]


def connect_hardware():
    import uProcess_x64 as up
    com_port = find_port_by_pid(ETP_DEVICE_PID)
    eib = up.CEIB()
    eib.InitConnection(com_port)
    channels = [eib.NewEP01(addr) for addr in WELL_ADDRESSES]
    return eib, channels, f"Connected — COM{com_port}"


def connect_mock():
    from c_ETP.mock_device import MockEIB
    eib = MockEIB()
    channels = [eib.NewEP01(addr) for addr in WELL_ADDRESSES]
    return eib, channels, "SIMULATION MODE (no hardware)"


def main():
    parser = argparse.ArgumentParser(description="ETP 4-well sequencer GUI")
    parser.add_argument("--simulate", action="store_true",
                         help="Run against a simulated device instead of real hardware")
    args = parser.parse_args()

    if args.simulate:
        eib, channels, device_label = connect_mock()
    else:
        try:
            eib, channels, device_label = connect_hardware()
        except Exception as exc:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("ETP Device Not Found", str(exc))
            root.destroy()
            sys.exit(1)

    app = App(channels, eib=eib, device_label=device_label)
    app.mainloop()


if __name__ == "__main__":
    main()
