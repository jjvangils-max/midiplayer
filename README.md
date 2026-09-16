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
- Coin: **JY-616** acceptor on an RS232-TTL serial line (9600 baud), binary  frame `48 45 xx` (`0x0A` = 1 EUR), read on the Pi UART RX.
- Relay: **gpiozero** optocoupler output (active-HIGH by default).
- USB: **pyudev** (event-driven import).

## Requirements

    sudo apt install libgl1 libegl1 libxkbcommon0
    pip install -r requirements.txt

## Configure

Edit `config.py`:

- `MIDI_DIR` — folder with `.mid`/`.midi` files (default `/home/shared/MidiFiles`).
- `COIN_SOURCE` / `COIN_SERIAL_PORT` / `COIN_SERIAL_BAUD` — JY-616 RS232-TTL
  acceptor. The device sends a 3-byte binary frame `0x48 0x45 0xXX` per coin
  (`0x0A`=1 EUR, `0x14`=2 EUR, `0x05`=0.50 EUR). One frame = one credit = one
  song; the value is reported via `on_coin_value`. Default port is the Pi UART
  RX `/dev/serial0` @ 9600 baud (via optocoupler). Set `COIN_SOURCE = "gpio"`
  to use a legacy pulse acceptor instead.
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

## JY-616 serial line via optocoupler (Pi UART RX)

The JY-616 talks **RS232-TTL at 9600 baud** (NOT a contact pulse). Connect its
TX line to the Pi's UART RX (BCM 15, `/dev/serial0`) through an optocoupler
that shifts the acceptor voltage to the Pi's 3V3 logic:

```
JY-616 TX --[ R ]--+--> optocoupler LED --> GND (JY-616 side)
                    |
    (optocoupler transistor side, Pi supply)
    Pi 3V3 --[ R_pull ]-- collector
    emitter --> Pi UART RX (BCM 15)
    emitter --> GND (Pi)
```

The optocoupler isolates the Pi's 3V3 logic from the acceptor's voltage and
brings the signal to the right level. Each accepted coin sends the 3-byte
frame `0x48 0x45 0xXX`; the app reads it as `/dev/serial0` @ 9600 baud.

Disable the console/getty on the Pi UART so the app owns it:

    sudo raspi-config → Interface Options → Serial Port → no login shell

The user running the app must be able to read the serial port:

    sudo usermod -aG dialout pi    # log out and back in

If you use a USB-RS232 adapter instead, the port is `/dev/ttyUSB0` — set
`COIN_SERIAL_PORT` accordingly.

## Serial port permissions (JY-616)

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

1. **JY-616 serial**: confirm the port (`/dev/serial0` for the Pi UART RX via
   optocoupler, `/dev/ttyUSB0` for a USB adapter) and the 9600 baud rate. The
   parser expects the binary frame `0x48 0x45 0xXX`; the value byte is scaled by
   `COIN_VALUE_SCALE` (default `10`, so `0x0A` -> 100 ct = 1 EUR) and reported
   via `on_coin_value`.
2. **Optocoupler relay**: drive it active-HIGH (Pi HIGH → LED on → output closed).
   If the optocoupler module inverts, set `RELAY_ACTIVE_HIGH = False`. The Pi
   3V3 pin current is fine for an optocoupler LED via its resistor — do not drive
   a relay coil directly from a GPIO.
3. Confirm the exact ALSA port names with `aconnect -l` (defaults match this Pi).
