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
# The JY-616 talks RS232-TTL (NOT a contact pulse). Each accepted coin sends a
# 3-byte binary frame on the serial line:
#   0x48 0x45 0xXX      (header bytes "HE" + one value byte)
# The value byte is the coin value in whole euros:
#   0x0A = 1 EUR, 0x14 = 2 EUR, 0x05 = 0.50 EUR, ...
# One complete frame equals one credit (one playable song); the value byte is
# scaled by COIN_VALUE_SCALE and reported via on_coin_value (euro cents).
#
# On a Raspberry Pi connect the JY-616 TX line to the Pi's UART RX (BCM 15,
# /dev/serial0) through an optocoupler that shifts the acceptor voltage to the
# Pi's 3V3 logic level. Keep the console/getty off that UART (raspi-config ->
# Interface Options -> Serial Port -> no login shell). If you use a USB-RS232
# adapter instead, the port is /dev/ttyUSB0.
COIN_SOURCE = "serial"        # "serial" (JY-616) or "gpio" (legacy pulse)
COIN_SERIAL_PORT = "/dev/serial0"  # Pi UART RX via optocoupler; /dev/ttyUSB0 for USB
COIN_SERIAL_BAUD = 9600       # JY-616 RS232-TTL baud rate
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
