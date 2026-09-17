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
- Coin: **JY-616** acceptor on a GPIO input (NO contact, internal pull-up);  pulse count encodes coin value, balance in euros.
- Relay: **gpiozero** optocoupler output (active-HIGH by default).
- USB: **pyudev** (event-driven import).

## Requirements

    sudo apt install libgl1 libegl1 libxkbcommon0
    pip install -r requirements.txt

## Configure

Edit `config.py`:

- `MIDI_DIR` — folder with `.mid`/`.midi` files (default `/home/shared/MidiFiles`).
- `COIN_SOURCE` / `COIN_GPIO` / `COIN_PULL_UP` / `COIN_ACTIVE_HIGH` — JY-616 coin
  acceptor on a GPIO input. The COIN output is a switch set to NO: in rest it
  is open (pin kept HIGH by the internal pull-up), and per coin it closes
  briefly to GND, producing a burst of pulses. Default `COIN_GPIO = 23`,
  `COIN_PULL_UP = True`, `COIN_ACTIVE_HIGH = False`.
- `COIN_BURST_WINDOW` / `COIN_PULSE_VALUE` / `SONG_PRICE_CENTS` — the number of
  pulses per coin encodes the coin value (set on the JY-616): 1=0.05, 2=0.10,
  3=0.20, 4=0.50, 5=1.00, 6=2.00 EUR. The reader adds the coin value to a euro
  balance; playing one song costs `SONG_PRICE_CENTS` (default 50 = 0.50 EUR),
  so a 2 EUR coin (6 pulses) buys 4 songs.
- `COIN_SOURCE = "serial"` (alternative) / `COIN_SERIAL_PORT` / `COIN_SERIAL_BAUD`
  — only for JY-616 variants that really emit a serial frame `0x48 0x45 0xXX`
  at 9600 baud (rare for the 616 family). See the serial section below.
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

## JY-616 coin input on a GPIO (NO contact, internal pull-up)

The JY-616 COIN output is a small switch, set to **NO (normally open)**: in
rest it is open, and per accepted coin it closes briefly to GND. This is an
open-collector-style output, not a real logic/RS-232 signal.

Read it on a Raspberry Pi GPIO with the **internal pull-up** enabled (no
optocoupler or divider needed when it is a true open-collector to GND):

```
JY-616 COIN (e.g. white wire) --> GPIO  (BCM 23)
JY-616 GND  <->  Pi GND        (must be connected)
```

With `COIN_PULL_UP = True` the Pi keeps the pin HIGH in rest (3V3 via the
internal pull-up); the NO switch pulls it LOW on a coin, so
`COIN_ACTIVE_HIGH = False`. The app counts the burst of LOW pulses per coin,
looks up the coin value in `COIN_PULSE_VALUE`, and adds it to a euro balance.
Playing one song costs `SONG_PRICE_CENTS` (0.50 EUR).

**Measure first.** With the acceptor on 12V, put a multimeter between COIN and
GND and drop a coin:
- 0V in rest and the pulse just shorts to GND → open-collector → use the
  pull-up wiring above (no divider).
- Pulse drives toward 5V → use a voltage divider, because 5V is too much for
  the Pi: 10kΩ above (COIN→GPIO) and 20kΩ below (GPIO→GND) gives ~3.3V.
- Pulse drives toward 12V → divider 27kΩ above / 10kΩ below (~3.25V). The
  10kΩ/4.7Ω pair gives 3.8V, which is too high.

In all cases the GND of the acceptor and the Pi must be connected, and the
acceptor supply must never reach a GPIO directly.

## JY-616 serial variant (alternative)

If your JY-616 variant really emits a serial frame `0x48 0x45 0xXX` at 9600
baud (rare for the 616 family), set `COIN_SOURCE = "serial"` and connect its
TX line to the Pi's UART RX (BCM 15, `/dev/serial0`) through an optocoupler
that shifts the acceptor voltage to 3V3. Disable the console/getty on the
UART (`sudo raspi-config` → Interface Options → Serial Port → no login
shell) and add the user to `dialout`. For a USB-RS232 adapter, the port is
`/dev/ttyUSB0`. One frame = one credit; the value byte is reported via
`on_coin_value`.

## Serial port permissions (JY-616)

## Behaviour summary

- **Library**: scans `MIDI_DIR` for `.mid`/`.midi`, sorts alphabetically, shows 8
  per page with ◀/▶ paging.
- **Select** a song → metadata (title/artist/copyright/tempo/length/tracks) is
  shown in the info panel together with the big **PLAY** button.
- **PLAY is only enabled after a coin**; the euro balance is shown top-center.
  One song costs 0.50 EUR; the balance decreases per song.
- While a song plays you may queue the next one (it plays after the 15 s gap).
- On first play (relay off): relay energised, **"Waiting for startup…"** for 45 s,
  then playback starts.
- After the last song: installation stays on 5 min, then the relay switches off.

## Mock mode

On a non-Pi machine (no gpiozero / no U4MIDI), the app still launches: GPIO is a
no-op mock and MIDI opens a virtual ALSA port. Use `hw.add_credits(50)` from a
Python shell to add 0.50 EUR and test coin logic if needed.

## Notes / things to confirm on hardware

1. **JY-616 coin (GPIO)**: with `COIN_SOURCE = "gpio"` confirm the COIN output
   is the NO contact to GND (measure: 0V in rest, brief short to GND per coin).
   With the internal pull-up the pin reads HIGH in rest and LOW on a coin, so
   `COIN_PULL_UP = True` and `COIN_ACTIVE_HIGH = False`. If your unit drives 5V
   or 12V, use a divider to 3V3 (see above). Set `COIN_PULSES_PER_CREDIT` to the
   number of pulses the acceptor emits per coin. No coin value is reported.
2. **JY-616 serial (alternative)**: only for variants that really emit a serial
   frame `0x48 0x45 0xXX` at 9600 baud. Confirm the port and baud; the value byte
   is scaled by `COIN_VALUE_SCALE` (default `10`, so `0x0A` -> 100 ct = 1 EUR).
3. **Optocoupler relay**: drive it active-HIGH (Pi HIGH → LED on → output closed).
   If the optocoupler module inverts, set `RELAY_ACTIVE_HIGH = False`. The Pi
   3V3 pin current is fine for an optocoupler LED via its resistor — do not drive
   a relay coil directly from a GPIO.
3. Confirm the exact ALSA port names with `aconnect -l` (defaults match this Pi).
