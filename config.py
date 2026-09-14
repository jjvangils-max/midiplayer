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

# --- Coin mechanism: JY-616 on serial -------------------------------------
# The JY-616 outputs on a serial line at 115200 baud. Each accepted coin sends
# a fixed string "4858" followed by a 2-digit value code:
#   "485810" = 1 euro, "485820" = 2 euro, "485805" = 0.50 euro, ...
# The value code is the coin value in units of 0.10 EUR ("xx" = value * 10 ct).
# Each line/feed equals one playable song (one credit).
COIN_SOURCE = "serial"        # "serial" (JY-616) or "gpio" (legacy pulse)
COIN_SERIAL_PORT = "/dev/ttyUSB0"   # JY-616 serial adapter; change if it is ttyAMA0
COIN_SERIAL_BAUD = 115200
COIN_SERIAL_PREFIX = "4858"   # header that precedes every coin message
COIN_VALUE_DIGITS = 2          # number of digits after the prefix

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
DEFAULT_LANG = "en"
LANGUAGES = ("en", "fr")
