#!/usr/bin/env python3
"""
kaco_monitor_mqtt.py

Same KACO RS485/Ethernet polling as kaco_monitor.py, but publishes readings
to an MQTT broker using Home Assistant's MQTT Discovery format. Once this
is running, the sensors just appear in Home Assistant automatically --
no manual YAML or entity configuration needed.

Sensors created in HA:
    sensor.kaco_inverter_ac_power        (W, instantaneous)
    sensor.kaco_inverter_dc_power        (W, instantaneous)
    sensor.kaco_inverter_temperature     (C, instantaneous)
    sensor.kaco_inverter_energy_today    (Wh, resets daily -- for display only)
    sensor.kaco_inverter_energy_total    (kWh, lifetime, ever-increasing)

That last one -- energy_total -- is the one to add to Home Assistant's
Energy Dashboard as a solar production source (Settings > Dashboards >
Energy > Add Solar Production). HA computes daily/monthly/yearly totals,
graphs, and comparisons from that single ever-increasing counter -- no
spreadsheet tallying needed.

REQUIREMENTS
    pip install pyserial paho-mqtt

This protocol and this script apply to the whole KACO "02xi" inverter
family -- 1502xi, 2502xi, 3502xi, and 5002xi (and their Schueco-badged
Powador equivalents) all speak the same RS485 ASCII protocol. Use
--model to identify your specific unit; it's cosmetic only (it just
names the device in Home Assistant) and doesn't change the protocol.

USAGE
    python kaco_monitor_mqtt.py \\
        --port socket://192.168.1.50:8899 \\
        --address 1 \\
        --interval 30 \\
        --model "blueplanet 5002xi" \\
        --mqtt-host 192.168.x.x \\
        --mqtt-user myuser \\
        --mqtt-password mypassword

    (Omit --mqtt-user/--mqtt-password if your broker allows anonymous
    connections. --mqtt-host is your Home Assistant / Mosquitto IP.)

    Run this continuously (e.g. as a systemd service, a scheduled task,
    or a Home Assistant add-on) on whatever always-on machine you want
    doing the polling.
"""

import argparse
import json
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("This script needs pyserial. Install it with:  pip install pyserial")

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("This script needs paho-mqtt. Install it with:  pip install paho-mqtt")


DISCOVERY_PREFIX = "homeassistant"

# Each entry: (unique_id_suffix, name, unit, device_class, state_class, value_key)
SENSORS = [
    ("ac_power", "AC Power", "W", "power", "measurement", "ac_watts"),
    ("dc_power", "DC Power", "W", "power", "measurement", "dc_watts"),
    ("temperature", "Temperature", "\u00b0C", "temperature", "measurement", "temp_c"),
    ("energy_today", "Energy Today", "Wh", "energy", "total", "energy_today_wh"),
    ("energy_total", "Energy Total", "kWh", "energy", "total_increasing", "energy_total_kwh"),
]


def build_command(address: int, cmd: int) -> bytes:
    return f"#{address:02d}{cmd}\r".encode("ascii")


def read_reply(ser: serial.Serial, timeout_s: float = 2.0) -> str:
    ser.timeout = timeout_s
    raw = ser.read_until(b"\r")
    return raw.decode("ascii", errors="replace").strip()


def poll_once(ser: serial.Serial, address: int, cmd: int):
    ser.reset_input_buffer()
    ser.write(build_command(address, cmd))
    return read_reply(ser)


def parse_cmd0(reply: str):
    parts = reply.lstrip("*").split()
    if len(parts) < 10:
        return None
    try:
        return {
            "dc_watts": float(parts[4]),
            "ac_watts": float(parts[7]),
            "temp_c": float(parts[8]),
            "energy_today_wh": float(parts[9]),
        }
    except ValueError:
        return None


def parse_cmd3(reply: str):
    parts = reply.lstrip("*").split()
    if len(parts) < 8:
        return None
    try:
        energy_total_kwh = float(parts[4]) / 10.0
    except ValueError:
        return None
    return {"energy_total_kwh": energy_total_kwh}


def is_plausible_energy_total(new_value: float, last_good_value: float) -> bool:
    """Reject readings that are implausible given the last known-good value.
    A corrupted/truncated serial reply can still parse as a valid float but
    be wildly wrong (e.g. a stray byte truncating the number) -- catch that
    by rejecting any reading that isn't a small, physically-reasonable step
    up from the last one, rather than trusting the raw parse in isolation."""
    max_plausible_jump_kwh = 5.0  # generous margin over one poll interval
    if new_value < last_good_value:
        return False  # lifetime counter should never decrease
    if (new_value - last_good_value) > max_plausible_jump_kwh:
        return False  # too big a jump to be real production in one interval
    return True


def slugify(text: str) -> str:
    """Turn a human-readable model name into a safe MQTT topic / unique_id segment."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "kaco_inverter"


def device_info(device_id: str, device_name: str, model: str):
    return {
        "identifiers": [device_id],
        "name": device_name,
        "manufacturer": "KACO new energy",
        "model": model,
    }


def publish_discovery(client: mqtt.Client, device_id: str, device_name: str, model: str,
                       state_topic: str, availability_topic: str):
    """Publish one HA MQTT Discovery config message per sensor. Retained so HA
    picks them up even if it wasn't running when this script started."""
    for suffix, name, unit, device_class, state_class, value_key in SENSORS:
        unique_id = f"{device_id}_{suffix}"
        topic = f"{DISCOVERY_PREFIX}/sensor/{device_id}/{suffix}/config"
        payload = {
            "name": name,
            "unique_id": unique_id,
            "state_topic": state_topic,
            "availability_topic": availability_topic,
            "value_template": f"{{{{ value_json.{value_key} }}}}",
            "unit_of_measurement": unit,
            "device_class": device_class,
            "state_class": state_class,
            "device": device_info(device_id, device_name, model),
        }
        client.publish(topic, json.dumps(payload), retain=True)


def main():
    ap = argparse.ArgumentParser(description="Poll a KACO inverter and publish to MQTT/Home Assistant")
    ap.add_argument("--port", required=True,
                     help="Serial port (COM5, /dev/ttyUSB0) or socket://<ip>:<port> for the EE11")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--address", type=int, default=1)
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--mqtt-host", required=True, help="MQTT broker IP (your HA/Mosquitto host)")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-user", default=None)
    ap.add_argument("--mqtt-password", default=None)
    ap.add_argument("--model", default="blueplanet 5002xi",
                     help="Your inverter's model name, e.g. 'blueplanet 3502xi' -- cosmetic "
                          "only, used to label the device in Home Assistant and to keep "
                          "multiple inverters' MQTT topics distinct if you run more than one")
    args = ap.parse_args()

    if args.interval < 1:
        sys.exit("Interval must be >= 1 second (KACO protocol minimum).")

    model_slug = slugify(args.model)
    device_id = f"kaco_{model_slug}_addr{args.address}"
    device_name = f"KACO {args.model}"
    state_topic = f"kaco/{device_id}/state"
    availability_topic = f"kaco/{device_id}/availability"

    client = mqtt.Client(client_id=device_id)
    if args.mqtt_user:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
    client.will_set(availability_topic, "offline", retain=True)

    print(f"Connecting to MQTT broker at {args.mqtt_host}:{args.mqtt_port} ...")
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    client.loop_start()

    publish_discovery(client, device_id, device_name, args.model, state_topic, availability_topic)
    client.publish(availability_topic, "online", retain=True)

    print(f"Opening {args.port} @ {args.baud} baud, polling inverter address {args.address} "
          f"every {args.interval}s. Ctrl+C to stop.")

    try:
        ser = serial.serial_for_url(args.port, baudrate=args.baud, bytesize=8,
                                     parity="N", stopbits=1, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"Could not open {args.port}: {e}")

    last_good_energy_total = None      # a value we've trusted and published
    pending_energy_total = None        # an unconfirmed candidate awaiting agreement

    try:
        while True:
            values = {}

            reply0 = poll_once(ser, args.address, 0)
            if reply0.startswith("*"):
                parsed = parse_cmd0(reply0)
                if parsed:
                    values.update(parsed)

            time.sleep(1)  # small gap between queries, per protocol minimum

            reply3 = poll_once(ser, args.address, 3)
            if reply3.startswith("*"):
                parsed3 = parse_cmd3(reply3)
                if parsed3:
                    candidate = parsed3["energy_total_kwh"]

                    if last_good_energy_total is None:
                        # No trusted baseline yet -- require two consecutive
                        # readings that agree closely before trusting either
                        # of them, so a single garbled reading right after
                        # startup can't get published as if it were real.
                        if pending_energy_total is not None and abs(candidate - pending_energy_total) < 0.5:
                            last_good_energy_total = candidate
                            values["energy_total_kwh"] = candidate
                        else:
                            print(f"energy_total reading {candidate} not yet confirmed "
                                  f"(no trusted baseline) -- waiting for agreement")
                        pending_energy_total = candidate
                    elif is_plausible_energy_total(candidate, last_good_energy_total):
                        values["energy_total_kwh"] = candidate
                        last_good_energy_total = candidate
                    else:
                        print(f"Rejected implausible energy_total reading: {candidate} "
                              f"(last good: {last_good_energy_total}) -- likely a garbled reply")

            if values:
                client.publish(state_topic, json.dumps(values), retain=True)
                print(f"Published: {values}")
            else:
                print("No valid reply this cycle -- skipping publish.")

            time.sleep(max(0, args.interval - 1))

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        client.publish(availability_topic, "offline", retain=True)
        client.loop_stop()
        client.disconnect()
        ser.close()


if __name__ == "__main__":
    main()
