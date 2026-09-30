#!/usr/bin/env python3
"""
kaco_monitor.py

Polls a KACO blueplanet xi-series inverter (e.g. 5002xi) directly over its
RS485 interface, using KACO's documented ASCII data-logger protocol -- the
same protocol the WatchDOG card itself uses internally. Logs readings to a
local CSV file so you can monitor production without relying on KACO's
(now-defunct) cloud portal.

REQUIREMENTS
    pip install pyserial

HARDWARE
    A USB-to-RS485 adapter, wired to the inverter's RS485 A / B / GND
    terminals (on the communication board behind the inverter's front
    door). If your WatchDOG card is still connected to those same
    terminals, disconnect it first -- two devices trying to act as RS485
    "master" at the same time will collide and neither will get clean
    data.

PROTOCOL NOTES
    - Command format sent to the inverter:  "#<addr><cmd>\r"
        addr = 2-digit inverter address (usually "01" unless you changed
               it via the inverter's front-panel Set menu)
        cmd  = single digit command number
    - Command "0" returns the "instant values" telegram:
        *<addr><cmd> <status> <dc_v> <dc_a> <dc_w> <ac_v> <ac_a> <ac_w>
        <temp_c> <e_day_wh> <type> <checksum>
    - Command "3" returns a secondary telegram with peak power / totals:
        *<addr><cmd> <p_peak_w> <e_day_wh> <counter> <e_total_0.1kwh>
        <hours_counter> <hours_today> <hours_total>
    - Default serial settings for this protocol family are typically
      9600 baud, 8 data bits, no parity, 1 stop bit. If you get garbage
      or no response, check the RS485 settings on the inverter's own
      front-panel Set/Configuration menu (Select interface and settings
      of RS485 address) and adjust BAUD below to match.
    - The KACO spec requires the polling interval to be >= 1 second.
      This script defaults to 30s, which is plenty for daily production
      monitoring and is gentle on the bus.

USAGE
    python kaco_monitor.py --port COM5 --address 1 --interval 30 --csv solar_log.csv

    (On Windows, find the COM port in Device Manager after plugging in the
    USB-RS485 adapter. On Linux/Mac it'll be something like /dev/ttyUSB0.)
"""

import argparse
import csv
import datetime
import os
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("This script needs pyserial. Install it with:  pip install pyserial")


CSV_FIELDS_CMD0 = [
    "timestamp",
    "raw_reply",
    "status",
    "dc_volts",
    "dc_amps",
    "dc_watts",
    "ac_volts",
    "ac_amps",
    "ac_watts",
    "temp_c",
    "energy_today_wh",
    "inverter_type",
]

CSV_FIELDS_CMD3 = [
    "timestamp",
    "raw_reply",
    "peak_power_w",
    "energy_today_wh",
    "counter",
    "energy_total_01kwh",
    "hours_counter",
    "hours_today",
    "hours_total",
]


def build_command(address: int, cmd: int) -> bytes:
    """Build a KACO ASCII command telegram, e.g. address=1, cmd=0 -> '#010\\r'"""
    return f"#{address:02d}{cmd}\r".encode("ascii")


def read_reply(ser: serial.Serial, timeout_s: float = 2.0) -> str:
    """Read one reply line from the inverter. Replies start with '*' and end CR (and
    sometimes are preceded by a leading LF from the previous telegram)."""
    ser.timeout = timeout_s
    raw = ser.read_until(b"\r")
    return raw.decode("ascii", errors="replace").strip()


def parse_cmd0(reply: str):
    """Parse an 'instant values' reply, e.g.:
    '*010 4 585.9 10.17 5958 229.5 24.90 5720 36 17614 9600I dx'
    """
    parts = reply.lstrip("*").split()
    # parts[0] is the echoed address+command, e.g. '010'
    if len(parts) < 10:
        return None
    try:
        return {
            "status": parts[1],
            "dc_volts": float(parts[2]),
            "dc_amps": float(parts[3]),
            "dc_watts": float(parts[4]),
            "ac_volts": float(parts[5]),
            "ac_amps": float(parts[6]),
            "ac_watts": float(parts[7]),
            "temp_c": float(parts[8]),
            "energy_today_wh": float(parts[9]),
            "inverter_type": parts[10] if len(parts) > 10 else "",
        }
    except ValueError:
        return None


def parse_cmd3(reply: str):
    """Parse the secondary telegram, e.g.:
    '*013 2286 4184 42 581 8:46 11:04 11:04'
    """
    parts = reply.lstrip("*").split()
    if len(parts) < 8:
        return None
    try:
        return {
            "peak_power_w": float(parts[1]),
            "energy_today_wh": float(parts[2]),
            "counter": parts[3],
            "energy_total_01kwh": float(parts[4]),
            "hours_counter": parts[5],
            "hours_today": parts[6],
            "hours_total": parts[7],
        }
    except ValueError:
        return None


def ensure_csv_header(path: str, fieldnames):
    new_file = not os.path.exists(path)
    if new_file:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()


def append_csv_row(path: str, fieldnames, row: dict):
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(row)


def poll_once(ser: serial.Serial, address: int, cmd: int):
    ser.reset_input_buffer()
    ser.write(build_command(address, cmd))
    reply = read_reply(ser)
    return reply


def main():
    ap = argparse.ArgumentParser(description="Poll a KACO blueplanet xi inverter over RS485")
    ap.add_argument("--port", required=True,
                     help="Serial port (e.g. COM5, /dev/ttyUSB0) OR a network address for a "
                          "serial-to-Ethernet device like the Elfin EE11, given as "
                          "socket://<ip>:<port> (e.g. socket://192.168.1.50:8899)")
    ap.add_argument("--baud", type=int, default=9600, help="Baud rate (default 9600)")
    ap.add_argument("--address", type=int, default=1, help="Inverter RS485 address (default 1)")
    ap.add_argument("--interval", type=int, default=30, help="Seconds between polls (default 30, min 1)")
    ap.add_argument("--csv", default="solar_log.csv", help="CSV file for instant-values log")
    ap.add_argument("--csv-extra", default="solar_log_totals.csv", help="CSV file for command-3 (totals/peak) log")
    ap.add_argument("--skip-extra", action="store_true", help="Only poll command 0 (instant values), skip command 3")
    args = ap.parse_args()

    if args.interval < 1:
        sys.exit("Interval must be >= 1 second (KACO protocol minimum).")

    ensure_csv_header(args.csv, CSV_FIELDS_CMD0)
    if not args.skip_extra:
        ensure_csv_header(args.csv_extra, CSV_FIELDS_CMD3)

    print(f"Opening {args.port} @ {args.baud} baud, polling inverter address {args.address} "
          f"every {args.interval}s. Ctrl+C to stop.")

    try:
        # serial_for_url transparently handles both plain COM ports/device paths
        # (e.g. "COM5", "/dev/ttyUSB0") and network addresses like
        # "socket://192.168.1.50:8899" for devices like the Elfin EE11.
        ser = serial.serial_for_url(args.port, baudrate=args.baud, bytesize=8,
                                     parity="N", stopbits=1, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    try:
        while True:
            now = datetime.datetime.now().isoformat(timespec="seconds")

            reply0 = poll_once(ser, args.address, 0)
            if reply0.startswith("*"):
                parsed = parse_cmd0(reply0)
                if parsed:
                    row = {"timestamp": now, "raw_reply": reply0, **parsed}
                    append_csv_row(args.csv, CSV_FIELDS_CMD0, row)
                    print(f"[{now}] AC {parsed['ac_watts']:.0f} W  "
                          f"DC {parsed['dc_watts']:.0f} W  "
                          f"Today {parsed['energy_today_wh']:.0f} Wh  "
                          f"Temp {parsed['temp_c']:.0f} C")
                else:
                    print(f"[{now}] Got a reply but couldn't parse it: {reply0!r}")
            else:
                print(f"[{now}] No / unexpected reply (got {reply0!r}). "
                      f"Check wiring, address, and baud rate.")

            if not args.skip_extra:
                time.sleep(1)  # small gap between the two queries, per protocol minimum
                reply3 = poll_once(ser, args.address, 3)
                if reply3.startswith("*"):
                    parsed3 = parse_cmd3(reply3)
                    if parsed3:
                        row3 = {"timestamp": now, "raw_reply": reply3, **parsed3}
                        append_csv_row(args.csv_extra, CSV_FIELDS_CMD3, row3)

            time.sleep(max(0, args.interval - (1 if not args.skip_extra else 0)))

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
