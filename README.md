# KACO RS485 Home Assistant Monitor

Local, cloud-free solar production monitoring for older KACO "02xi"-series
string inverters — for when the manufacturer's cloud monitoring portal has
died, been discontinued, or changed hands and dropped support for your
hardware.

This started as a project to revive monitoring on a 13-year-old **KACO
blueplanet 5002xi** after its cloud portal went away, its original installer
went out of business, and its "WatchDOG" data logger (actually a rebadged
**Powador Gateway / Meteocontrol WEB'log**) turned out to be dead and
inaccessible. Rather than depend on that logger at all, this polls the
inverter directly over its RS485 service bus, using KACO's own documented
ASCII protocol.

## Is this for you?

This should work for the whole KACO **"02xi" family**: 1502xi, 2502xi,
3502xi, and 5002xi, including Schueco-badged Powador equivalents — they all
speak the same RS485 ASCII protocol. It applies whether or not your
inverter's original data logger (WatchDOG / Powador Gateway / similar) is
still working; you're bypassing it entirely and talking to the inverter's
communication board directly, via a **second, usually-unused RS485 terminal
block** most of these units have alongside the one their factory logger
uses.

**If your inverter has built-in Ethernet** (a later-generation KACO with a
network port on the inverter itself, no separate RS485 logger box), you
likely don't need this — check out
[KoljaWindeler/kaco](https://github.com/KoljaWindeler/kaco) instead, which
targets that newer, Ethernet-native generation directly.

## How it works

```
KACO inverter (RS485 comm board)
        │  RS485 A/B/GND
        ▼
Elfin EE11 (RS485-to-Ethernet bridge, "TCP Server" mode)
        │  Ethernet / your LAN
        ▼
kaco_monitor_mqtt.py  (polls the inverter's ASCII protocol every N seconds)
        │  MQTT (Home Assistant auto-discovery)
        ▼
Home Assistant  (sensors + Energy Dashboard)
```

Two ways to run the script are included:

- **`scripts/kaco_monitor.py`** — standalone, no Home Assistant or MQTT
  required. Logs readings to a local CSV file. Good for a first bench test,
  or if you just want a spreadsheet.
- **`scripts/kaco_monitor_mqtt.py`** / **`homeassistant-addon/`** — publishes
  readings to MQTT using Home Assistant's MQTT Discovery format, so sensors
  appear in HA automatically. This is the one that feeds the Energy
  Dashboard. It's provided both as a plain script (run it anywhere Python
  runs) and as a self-contained Home Assistant add-on.

## Hardware you'll need

- A way to reach the inverter's RS485 terminals: for bench testing, a
  cheap USB-to-RS485 adapter (~$15). For a permanent, always-on setup, an
  Elfin EE11 RS485-to-Ethernet bridge (~$20, ships from China, budget a
  couple weeks) works well and is what this was built and tested against.
- Three wires: RS485 **A**, **B**, and **GND**, run from your adapter/bridge
  to the inverter's spare RS485 terminal block.
- **If A/B produce no data or garbage**, swap them — RS485 polarity labeling
  is not consistent across manufacturers, and you may need to swap it at
  more than one point in the chain (it's harmless to try).

⚠️ **Safety note:** opening an inverter's communications compartment
generally means working near — though not touching — components carrying
DC string voltage and mains AC. If you're not confident identifying and
staying clear of the high-voltage sections, have someone qualified handle
the physical wiring. This project is about the data path, not an invitation
to work carelessly inside a live inverter.

## Protocol notes

- Command format sent to the inverter: `"#<addr><cmd>\r"` — `addr` is a
  2-digit inverter address (usually `01`), `cmd` is a single-digit command
  number.
- `cmd 0` returns an "instant values" telegram (AC/DC volts, amps, watts,
  temperature, energy today).
- `cmd 3` returns peak power and lifetime energy totals.
- Default serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit.
- The protocol requires a minimum ~1 second between polls; these scripts
  default to a gentler 30 seconds, which is plenty for production
  monitoring.

Full command construction and parsing is in `scripts/kaco_monitor.py`,
which is the simplest place to read the protocol handling.

## Quick start: standalone CSV logging

```bash
pip install pyserial
python scripts/kaco_monitor.py --port COM5 --address 1 --interval 30 --csv solar_log.csv
```

Use `--port socket://<ip>:<port>` instead of a COM port if you're going
through a network bridge like the EE11 (e.g. `socket://192.168.1.50:8899`).

## Quick start: MQTT / Home Assistant, as a plain script

```bash
pip install pyserial paho-mqtt
python scripts/kaco_monitor_mqtt.py \
    --port socket://192.168.1.50:8899 \
    --address 1 \
    --interval 30 \
    --model "blueplanet 5002xi" \
    --mqtt-host 192.168.x.x \
    --mqtt-user myuser \
    --mqtt-password mypassword
```

`--model` is cosmetic — it labels the device in Home Assistant and keeps
topics distinct if you run more than one inverter. It doesn't change how
the protocol works.

Sensors created in Home Assistant:

| Entity suffix   | Description                          | Unit | State class        |
|------------------|---------------------------------------|------|---------------------|
| `ac_power`       | AC output power (instantaneous)       | W    | measurement         |
| `dc_power`       | DC input power (instantaneous)        | W    | measurement         |
| `temperature`    | Inverter temperature                  | °C   | measurement         |
| `energy_today`   | Energy produced today (resets daily)  | Wh   | total               |
| `energy_total`   | Lifetime energy produced              | kWh  | total_increasing    |

Add **`energy_total`** to Home Assistant's Energy Dashboard (Settings →
Dashboards → Energy → Add Solar Production) — HA computes daily / monthly /
yearly totals and graphs from that one ever-increasing counter.

## Quick start: as a Home Assistant add-on

1. Copy the `homeassistant-addon/` folder to `/addons/local/kaco_monitor/`
   on your Home Assistant host (its own folder under `local`, not directly
   under `/addons/`, or the Supervisor won't pick it up).
2. In Home Assistant, go to the add-on store's Local section, find "KACO
   Inverter Monitor," and install it.
3. Set its configuration options: `inverter_port` (your serial device or
   `socket://ip:port`), `inverter_address`, `inverter_model`, and
   `poll_interval`.
4. Start it. It will automatically pull your MQTT broker credentials from
   Home Assistant's own MQTT service (e.g. the Mosquitto add-on) — no
   broker details to hardcode.

### Updating the add-on after editing its files

Home Assistant's Supervisor builds the add-on into a Docker image at
install/rebuild time. **Editing the files on disk and clicking "Restart"
does not pick up your changes** — the running container is still the old
image. You need **Rebuild**, not Restart, any time you change the add-on's
Python script or Dockerfile. This tripped us up more than once during
development.

If you don't see an "update available" prompt after bumping the version in
`config.yaml`, try stopping the add-on and checking for updates again — the
Supervisor sometimes needs that nudge to re-scan a local add-on's manifest.

### Upgrading from a version with different device naming

If you're upgrading and the add-on's entity-naming scheme changes (as it
did between early hardcoded-name versions and the current `--model`-based
naming), Home Assistant will treat the newly-named entities as brand new,
and your old ones will show "unavailable." Your historical Energy Dashboard
statistics aren't deleted, but they're keyed to the old entity_id, so the
graph will show a gap unless you either keep the `--model` value identical
across the upgrade, or merge the statistics by renaming the new entity back
to the old entity_id (after removing the old, orphaned entity from the
registry) so Home Assistant continues writing to the same historical
series.

## Troubleshooting

- **No reply, or garbage data:** check RS485 A/B wiring — try swapping
  them. Confirm baud rate matches your inverter's own RS485 setting
  (front-panel Set/Configuration menu).
- **SSH "Corrupted MAC on input" when connecting to Home Assistant on
  older/ARM hardware:** force a mutually-supported cipher, e.g.
  `ssh -o MACs=hmac-sha2-256-etm@openssh.com root@<ha-host>`.
- **`scp` refuses to transfer files even though interactive SSH works:**
  some HA SSH add-on configurations allow an interactive shell but not
  SFTP/SCP for the same account. Workaround: create/update files directly
  over the SSH session with a heredoc, e.g.
  `cat > filename << 'EOF' ... EOF`.
- **Local add-on doesn't show up in the store:** it must live at
  `/addons/local/<slug>/`, not `/addons/<slug>/`. Also check
  `ha supervisor logs` (or Settings → System → Logs) for a `config.yaml`
  schema validation error — an empty string in a field that expects a
  proper URL or is otherwise optional (e.g. a stray `url: ""`) will fail
  validation silently from the UI's perspective.
- **Occasional wild energy spikes / implausible readings:** a garbled
  serial reply can still parse as a syntactically valid number while being
  wrong. `kaco_monitor_mqtt.py` guards against this for the lifetime energy
  counter by requiring two consecutive readings to agree before trusting a
  baseline, and rejecting any subsequent reading that decreases or jumps
  implausibly far in one interval — worth keeping in mind if you build
  additional sanity checks for other fields.

## Prior art

[KoljaWindeler/kaco](https://github.com/KoljaWindeler/kaco) is a great
existing solution — for **newer KACO inverters with built-in Ethernet**,
which speak a different transport entirely. This project exists because
older RS485-only units like the 02xi family have no such built-in network
interface and needed a different approach: a physical RS485 bridge plus
this protocol implementation. If your inverter has an Ethernet port,
start with that project instead.

## License

MIT — see [LICENSE](LICENSE).
