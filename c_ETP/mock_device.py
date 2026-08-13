import math
import random
import time

INTERLOCK_OPEN_BIT = 0x80


class MockChannel:
    """Stands in for a CEP01 channel object so the GUI can be exercised
    without an EIB connected. Voltage ramps toward the commanded setpoint
    on a simple exponential curve, which is enough to exercise live
    plotting and the collection cutoff/alert path.
    """

    def __init__(self, address, ramp_seconds=6.0):
        self.address = address
        self.ramp_seconds = ramp_seconds
        self._watts = 0.0
        self._current_limit_mA = 0.0
        self._voltage_limit_V = 0.0
        self._polarity = "positive"
        self._segment_start = None
        self._segment_start_voltage = 0.0
        self._voltage = 0.0
        self._interlock_open = False

    def CmdSetPower(self, watts):
        self._watts = watts

    def CmdSetCurrent(self, mA):
        self._current_limit_mA = mA

    def CmdSetVoltage(self, volts):
        self._segment_start_voltage = self._voltage
        self._voltage_limit_V = volts
        self._segment_start = time.time()

    def CmdSetPositive(self):
        self._polarity = "positive"

    def CmdSetNegative(self):
        self._polarity = "negative"

    def CmdGetStatus(self):
        return True

    def GetOutputState(self):
        return INTERLOCK_OPEN_BIT if self._interlock_open else 0

    def GetVoltage(self):
        if self._segment_start is None:
            return self._voltage
        elapsed = time.time() - self._segment_start
        target = self._voltage_limit_V
        frac = 1 - math.exp(-elapsed / self.ramp_seconds)
        self._voltage = self._segment_start_voltage + (target - self._segment_start_voltage) * frac
        return max(0.0, self._voltage + random.uniform(-1.0, 1.0))

    def GetPower(self):
        return max(0.0, self._watts * random.uniform(0.95, 1.05))

    def GetCurrent(self):
        return max(0.0, self._current_limit_mA * random.uniform(0.9, 1.0))

    def CmdStop(self):
        self._segment_start = None
        self._voltage = 0.0
        self._voltage_limit_V = 0.0

    def set_interlock_open(self, open_):
        """Test hook: simulate the safety interlock switch opening."""
        self._interlock_open = open_


class MockEIB:
    """Stands in for up.CEIB() when no physical device is connected."""

    def InitConnection(self, com_port):
        return 0

    def NewEP01(self, address):
        return MockChannel(address)

    def CloseConnection(self):
        return 0
