# MIDI Player (Raspberry Pi + 10.1" touchscreen)

A touchscreen kiosk that lists MIDI files, plays them on a **CME U4MIDI WC**
(both DIN outputs), requires a coin-pulse to play, manages the motor/installation
relay (45 s warm-up, 15 s gap, 5 min cooldown), lets the user queue a next song,
imports MIDI files from a USB stick, and switches EN/FR via flags.

## Language / tech

- **Python 3** (recommended for Pi + GPIO + MIDI + GUI + USB).
- GUI: **PySide6 (Qt)** — fullscreen, large touch buttons.
- MIDI: **mido + python-rtmidi** (ALSA), plays SMF to both U4MIDI WC DIN outputs
  (ALSA client `U4MIDI WC`, ports `U4MIDI WC MIDI 1` and `U4MIDI WC MIDI 2`).
- Coin: **JY-616** acceptor on a serial line (115200 baud), format `4858xx`.
- Relay: **gpiozero** optocoupler output (active-HIGH by default).
- USB: **pyudev** (event-driven import).

## Requirements

    sudo apt install libgl1 libegl1 libxkbcommon0
    pip install -r requirements.txt

## Configure

Edit `config.py`:

- `MIDI_DIR` — folder with `.mid`/`.midi` files (default `/home/shared/MidiFiles`).
- `COIN_SOURCE` / `COIN_SERIAL_PORT` / `COIN_SERIAL_BAUD` — JY-616 serial acceptor
  (default `/dev/ttyUSB0` @ 115200). The device sends `4858` + 2-digit value code
  per coin (`485810`=1 EUR, `485820`=2 EUR, `485805`=0.50 EUR). One message = one
  credit = one song. Set `COIN_SOURCE = "gpio"` to use a legacy pulse acceptor.
- `RELAY_GPIO` / `RELAY_ACTIVE_HIGH` — optocoupler relay pin & polarity. The
  optocoupler input is a LED, so active-HIGH is normal; flip if your module inverts.
- `MIDI_OUT_PORT_NAMES` — exact ALSA port names of the U4MIDI WC DIN outputs.
  Defaults match the detected `aconnect -l` output (`U4MIDI WC MIDI 1/2`).
- `WARMUP_TIME` / `GAP_TIME` / `COOLDOWN_TIME` — 45 / 15 / 300 s by default.

## Run manually

    python3 main.py

## Autostart as kiosk

    sudo cp midiplayer.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now midiplayer.service

## Verify the U4MIDI WC ports

    aconnect -l

The U4MIDI WC appears as an ALSA kernel client (e.g. client 28) with two ports:
`U4MIDI WC MIDI 1` and `U4MIDI WC MIDI 2`. The app opens both by exact name and
plays to them simultaneously. If `aconnect -l` shows different names, update
`MIDI_OUT_PORT_NAMES` in `config.py`. If no matching hardware port is found, the
app opens a virtual ALSA port so the GUI still runs.

## Serial port permissions (JY-616)

The user running the app must be able to read `/dev/ttyUSB0` (or your port):

    sudo usermod -aG dialout pi
    # log out and back in, then:
    python3 -c "import serial; s=serial.Serial('/dev/ttyUSB0',115200); print(s)"

If your adapter is on the Pi's GPIO UART, the port is `/dev/serial0` (or
`/dev/ttyAMA0`) — set `COIN_SERIAL_PORT` accordingly. Keep the console/getty off
that UART (`sudo raspi-config` → Interface Options → Serial Port → no login shell).

## Behaviour summary

- **Library**: scans `MIDI_DIR` for `.mid`/`.midi`, sorts alphabetically, shows 8
  per page with ◀/▶ paging.
- **Select** a song → metadata (title/artist/copyright/tempo/length/tracks) is
  shown in the info panel together with the big **PLAY** button.
- **PLAY is only enabled after a coin**; the credit counter is shown top-center.
- While a song plays you may queue the next one (it plays after the 15 s gap).
- On first play (relay off): relay energised, **"Waiting for startup…"** for 45 s,
  then playback starts.
- After the last song: installation stays on 5 min, then the relay switches off.

## Mock mode

On a non-Pi machine (no gpiozero / no U4MIDI), the app still launches: GPIO is a
no-op mock and MIDI opens a virtual ALSA port. Use `hw.add_credits(1)` from a
Python shell to test coin logic if needed.

## Notes / things to confirm on hardware

1. **JY-616 serial**: confirm the port (`/dev/ttyUSB0` vs `/dev/serial0`) and
   baud. The parser treats every `4858xx` message as one credit; `xx` is the
   coin value in tenths of a euro and is reported via `on_coin_value`.
2. **Optocoupler relay**: drive it active-HIGH (Pi HIGH → LED on → output closed).
   If the optocoupler module inverts, set `RELAY_ACTIVE_HIGH = False`. The Pi
   3V3 pin current is fine for an optocoupler LED via its resistor — do not drive
   a relay coil directly from a GPIO.
3. Confirm the exact ALSA port names with `aconnect -l` (defaults match this Pi).
