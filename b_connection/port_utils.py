import serial.tools.list_ports

def find_port_by_serial(serial_number):
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if p.serial_number == serial_number:
            return p.device
    raise RuntimeError(f"Device with serial {serial_number} not found")

def find_port_by_pid(pid):
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if p.pid == pid:
            com_num = p.device.split('COM')[1]
            return int(com_num)
    raise RuntimeError(f"ETP Device Not Found. Check USB Connection and Try Again")
