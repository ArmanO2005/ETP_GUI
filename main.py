import uProcess_x64 as up
import matplotlib
matplotlib.use("Agg")

from b_connection.port_utils import find_port_by_pid
from c_ETP.ETP_utils import *

com_port = find_port_by_pid(60000)

eib = up.CEIB()
eib.InitConnection(com_port)

addresses = [1, 2, 3, 4]
channels = [eib.NewEP01(addr) for addr in addresses]

run_channels(channels, [0], "a_config/example_seq_config.yaml")

