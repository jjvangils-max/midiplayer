"""Central configuration for the MIDI player.

All hardware-dependent values are collected here so they can be changed
without touching the application logic. Defaults are tuned for the actual
Raspberry Pi build.
"""

from pathlib import Path

# --- Paths ----------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
MIDI_DIR = Path("/home/shared/MidiFiles")
USB_MOUNT_ROOT = Path("/media")          # udisks2 mounts under /media/<user>
SETTINGS_FILE = APP_DIR / "settings.json"

# --- Screen ---------------------------------------------------------------
SCREEN_W = 1024
SCREEN_H = 600
SONGS_PER_PAGE = 8

# --- Timing (seconds) -----------------------------------------------------
WARMUP_TIME = 45        # hold relay on, show "Waiting for startup" before first play
GAP_TIME = 15           # pause between songs
COOLDOWN_TIME = 300     # keep installation on this long after the last song, then relay off

# --- Coin mechanism: JY-616 on a GPIO input (NO contact, pull-up) ----------
# The JY-616 COIN output is a small switch (set to NO = normally open): in
# rest it is open, and per accepted coin it closes briefly to GND. This is an
# open-collector-style output, NOT a real RS-232/TTL logic signal.
#
# Read it on a Raspberry Pi GPIO with the INTERNAL PULL-UP enabled:
#   - COIN (e.g. white wire) -> GPIO (default BCM 23)
#   - GND of the acceptor <-> GND of the Pi (must be connected)
#   - pull_up = True  -> the Pi keeps the pin HIGH in rest (3V3 via pull-up)
#   - active_high = False -> each coin pulls the pin LOW (NO switch to GND)
#   - one pulse = one credit = one song (COIN_PULSES_PER_CREDIT = 1)
#
# If, after measuring, your unit really drives 5V or 12V on COIN, do NOT wire
# that directly to a GPIO: use a voltage divider or optocoupler to bring it to
# 3V3. See README for divider values. The GND of the acceptor and the Pi must
# always be connected; the acceptor supply must never reach a GPIO directly.
COIN_SOURCE = "gpio"          # "gpio" (JY-616 NO contact) or "serial"
COIN_GPIO = 23                # BCM pin reading the COIN output
COIN_PULL_UP = True           # internal pull-up; NO switch pulls the pin LOW
COIN_ACTIVE_HIGH = False      # NO switch to GND -> pulse is LOW-true
COIN_DEBOUNCE = 0.02          # gpiozero bounce_time (s) to filter contact bounce
COIN_BURST_WINDOW = 0.20      # seconds to group rapid pulses into one coin
COIN_PULSES_PER_CREDIT = 1    # pulses counted for one credit (one song)

# --- Coin mechanism: JY-616 on serial (alternative) -----------------------
# If your JY-616 variant really emits a serial frame (rare for the 616 family),
# set COIN_SOURCE = "serial" and use these. The device sends a 3-byte binary
# frame 0x48 0x45 0xXX (0x0A = 1 EUR) on the serial line at 9600 baud.
COIN_SERIAL_PORT = "/dev/serial0"  # Pi UART RX; /dev/ttyUSB0 for a USB adapter
COIN_SERIAL_BAUD = 9600       # JY-616 serial baud rate
COIN_SERIAL_PREFIX = b"\x48\x45"  # 2-byte header (0x48 0x45 = "HE")
COIN_SERIAL_VALUE_BYTES = 1   # one value byte follows the header
COIN_VALUE_SCALE = 10        # value byte * 10 = euro cents (0x0A -> 100 ct)

# --- Relay (motor / installation power) via optocoupler -------------------
# Driven through an optocoupler: the Pi GPIO drives the optocoupler LED side.
# The optocoupler input is a LED, so active-HIGH (Pi HIGH -> LED on -> output
# closed) is the normal wiring. If your optocoupler module inverts, flip this.
RELAY_GPIO = 17
RELAY_ACTIVE_HIGH = True

# --- MIDI ------------------------------------------------------------------
# CME U4MIDI WC on this Pi appears in ALSA as client 28 with two ports:
#   "U4MIDI WC MIDI 1"  (DIN OUT 1)
#   "U4MIDI WC MIDI 2"  (DIN OUT 2)
# rtmidi returns the full names; we match by substring. Both are played
# simultaneously so the MIDI goes to both DIN outputs.
MIDI_OUT_PORT_NAMES = ("U4MIDI WC MIDI 1", "U4MIDI WC MIDI 2")
MIDI_NUM_OUTS = 2
TEMPO_SCALE = 1.0     # 1.0 = play at the tempo stored in the file

# --- Languages ------------------------------------------------------------
DEFAULT_LANG = "fr"
LANGUAGES = ("en", "fr")
