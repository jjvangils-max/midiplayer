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
WARMUP_TIME = 20        # hold relay on, show countdown before first play
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
#
# The number of pulses per coin encodes the coin VALUE. The mapping below is
# set on the JY-616 via its DIP switches / pulse-count table:
#   1 pulse = 0.05 EUR, 2 = 0.10, 3 = 0.20, 4 = 0.50, 5 = 1.00, 6 = 2.00
# One accepted coin produces one burst of N pulses; we look up its value and
# add it to the balance in euro cents.
#
# Playing one song costs SONG_PRICE_CENTS (default 0.50 EUR). A 2 EUR coin
# (6 pulses) adds 200 ct, so 4 songs can be queued/played.
COIN_SOURCE = "gpio"          # "gpio" (JY-616 NO contact) or "serial"
COIN_GPIO = 23                # BCM pin reading the COIN output
COIN_PULL_UP = True           # internal pull-up; NO switch pulls the pin LOW
COIN_ACTIVE_HIGH = False      # NO switch to GND -> pulse is LOW-true
COIN_DEBOUNCE = 0.02          # gpiozero bounce_time (s) to filter contact bounce
COIN_MIN_PULSE = 0.008        # min pulse duration (s) to count; shorter spikes
                             # (cable touch / noise) are ignored
COIN_BURST_WINDOW = 0.40      # seconds to group one coin's pulses (gap > window)
COIN_PULSE_VALUE = {          # pulses -> euro cents for the accepted coin
    1: 5,
    2: 10,
    3: 20,
    4: 50,
    5: 100,
    6: 200,
}
SONG_PRICE_CENTS = 50         # one song costs 0.50 EUR

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
