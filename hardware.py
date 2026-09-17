"""Hardware layer: relay output + credit counting.

Coin input is handled by a pluggable "coin reader":
  * GpioCoinReader    -> JY-616 NO contact on a GPIO pin (pull-up, count pulses)
  * SerialCoinReader  -> JY-616 serial variant on a serial line (9600 baud)
  * Mock (no reader)  -> credits added programmatically (dev/test)

Falls back gracefully (no-op) when gpiozero / serial ports are unavailable so
the GUI still runs on a development machine. The interface stays identical.
"""

import threading
import time

import config

try:
    from gpiozero import Button, OutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory  # noqa: F401  (preferred on Bookworm)
    _HAS_GPIO = True
except Exception:
    _HAS_GPIO = False

try:
    import serial  # pyserial
    _HAS_SERIAL = True
except Exception:
    _HAS_SERIAL = False


class HardwareSignals:
    def on_coin(self, balance_cents): pass  # new balance in euro cents
    def on_relay(self, on): pass             # True=on, False=off
    def on_coin_value(self, cents): pass    # value of the last coin in euro cents


# ---------------------------------------------------------------------------
# Coin readers
# ---------------------------------------------------------------------------
class CoinReader:
    """Base: owns the credit counter. Subclasses call self._report_coin()."""
    def __init__(self, hardware):
        self.hw = hardware
        self._stop = threading.Event()

    def start(self):
        pass

    def stop(self):
        self._stop.set()

    def _report_coin(self, cents=None):
        """Register one accepted coin.

        cents = the coin value in euro cents (from the pulse/value table).
        Adds it to the balance and notifies the UI with the new balance.
        """
        value = cents or 0
        with self.hw._lock:
            self.hw.balance_cents += value
            balance = self.hw.balance_cents
        self.hw.signals.on_coin(balance)
        if value:
            self.hw.signals.on_coin_value(value)


class SerialCoinReader(CoinReader, threading.Thread):
    """JY-616 serial acceptor (RS232-TTL).

    The device emits a fixed header (default 0x48 0x45 = "HE") followed by one
    value byte for each accepted coin. We buffer incoming bytes and emit one
    credit whenever a complete frame is seen. The value byte (e.g. 0x0A = 1 EUR)
    is scaled by COIN_VALUE_SCALE and reported via on_coin_value.
    """

    def __init__(self, hardware):
        CoinReader.__init__(self, hardware)
        threading.Thread.__init__(self, daemon=True)
        self._ser = None

    def start(self):
        if not _HAS_SERIAL:
            return
        try:
            self._ser = serial.Serial(
                config.COIN_SERIAL_PORT,
                config.COIN_SERIAL_BAUD,
                timeout=0.5,
            )
        except Exception:
            self._ser = None
            return
        threading.Thread.start(self)

    def stop(self):
        CoinReader.stop(self)
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass

    def available(self):
        return self._ser is not None

    def run(self):
        buf = b""
        prefix = config.COIN_SERIAL_PREFIX
        nval = config.COIN_SERIAL_VALUE_BYTES
        while not self._stop.is_set() and self._ser is not None:
            try:
                chunk = self._ser.read(64)
            except Exception:
                chunk = b""
            if not chunk:
                continue
            buf += chunk
            # keep only the tail that could still contain a partial prefix
            while True:
                idx = buf.find(prefix)
                if idx < 0:
                    # keep last (len(prefix)-1) bytes in case of partial prefix
                    buf = buf[-(len(prefix) - 1):] if len(buf) >= len(prefix) else buf
                    break
                msg_start = idx + len(prefix)
                msg_end = msg_start + nval
                if len(buf) < msg_end:
                    # wait for more bytes
                    buf = buf[idx:]
                    break
                # value is a single binary byte (0x0A = 1 EUR, 0x14 = 2 EUR, ...)
                value_byte = buf[msg_start:msg_end][0]
                cents = value_byte * config.COIN_VALUE_SCALE
                self._report_coin(cents)
                buf = buf[msg_end:]


class GpioCoinReader(CoinReader, threading.Thread):
    """JY-616 acceptor on a GPIO input (NO contact, open-collector).

    The COIN output is a switch set to NO: in rest it is open (pin kept HIGH by
    the internal pull-up), and per accepted coin it closes briefly to GND,
    producing a burst of LOW pulses. The number of pulses encodes the coin
    value (COIN_PULSE_VALUE). We count pulses over a short burst window
    (COIN_BURST_WINDOW), look up the value, and add it to the euro balance.
    The coin value is reported via on_coin_value.
    """

    def __init__(self, hardware):
        CoinReader.__init__(self, hardware)
        threading.Thread.__init__(self, daemon=True)
        self._button = None
        self._pulse_q = []           # timestamps of pulses in the current burst
        self._wake = threading.Event()

    def start(self):
        if not _HAS_GPIO:
            return
        try:
            self._button = Button(
                config.COIN_GPIO,
                pull_up=config.COIN_PULL_UP,
                bounce_time=config.COIN_DEBOUNCE,
            )
            # NO switch to GND: the pulse pulls the pin LOW, so it is LOW-true.
            self._button.when_activated = self._on_pulse if config.COIN_ACTIVE_HIGH else None
            self._button.when_deactivated = self._on_pulse if not config.COIN_ACTIVE_HIGH else None
        except Exception:
            self._button = None
            return
        threading.Thread.start(self)

    def stop(self):
        CoinReader.stop(self)
        self._wake.set()
        if self._button is not None:
            try:
                self._button.close()
            except Exception:
                pass

    def available(self):
        return self._button is not None

    def _on_pulse(self):
        """Called from gpiozero's pin thread; just records + wakes the worker."""
        with self.hw._lock:
            self._pulse_q.append(time.monotonic())
        self._wake.set()

    def run(self):
        window = config.COIN_BURST_WINDOW
        value_table = config.COIN_PULSE_VALUE
        margin = 0.05
        poll = max(0.02, window / 5.0)
        while not self._stop.is_set():
            self._wake.wait(timeout=poll)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self.hw._lock:
                if not self._pulse_q:
                    continue
                now = time.monotonic()
                # pulses whose burst window has closed form a coin; keep the
                # rest and wait for the window to settle.
                burst = [t for t in self._pulse_q if now - t >= window - margin]
                if not burst:
                    continue
                self._pulse_q = [t for t in self._pulse_q if now - t < window - margin]
                pulses = len(burst)
            # map the number of pulses to the coin value (euro cents).
            cents = value_table.get(pulses)
            if cents:
                self._report_coin(cents)


# ---------------------------------------------------------------------------
# Hardware: relay + credits + coin reader
# ---------------------------------------------------------------------------
class Hardware:
    def __init__(self, signals=None):
        self.signals = signals or HardwareSignals()
        self.balance_cents = 0
        self._relay_on = False
        self._lock = threading.Lock()
        self._relay = None
        self._coin_reader = None
        self._setup_relay()
        self._setup_coin()

    # ---- setup ------------------------------------------------------------
    def _setup_relay(self):
        if not _HAS_GPIO:
            return
        try:
            self._relay = OutputDevice(
                config.RELAY_GPIO,
                active_high=config.RELAY_ACTIVE_HIGH,
                initial_value=False,
            )
        except Exception:
            self._relay = None

    def _setup_coin(self):
        if config.COIN_SOURCE == "serial":
            self._coin_reader = SerialCoinReader(self)
        elif config.COIN_SOURCE == "gpio":
            self._coin_reader = GpioCoinReader(self)
        else:
            self._coin_reader = None
        if self._coin_reader is not None:
            self._coin_reader.start()

    # ---- coin -------------------------------------------------------------
    def add_credits(self, cents=50):
        """Test hook: simulate a coin (default 0.50 EUR) without hardware."""
        with self._lock:
            self.balance_cents += cents
            balance = self.balance_cents
        self.signals.on_coin(balance)

    def consume_credit(self):
        """Try to charge one song. Returns True if the balance was enough."""
        price = config.SONG_PRICE_CENTS
        with self._lock:
            if self.balance_cents >= price:
                self.balance_cents -= price
                balance = self.balance_cents
                return True
            balance = self.balance_cents
        self.signals.on_coin(balance)
        return False

    def set_credits(self, cents):
        with self._lock:
            self.balance_cents = max(0, cents)
            balance = self.balance_cents
        self.signals.on_coin(balance)

    def coin_reader_available(self):
        return self._coin_reader is not None and getattr(self._coin_reader, "available", lambda: False)()

    # ---- relay ------------------------------------------------------------
    def relay_on(self):
        if self._relay is not None:
            try:
                self._relay.on()
            except Exception:
                pass
        self._relay_on = True
        self.signals.on_relay(True)

    def relay_off(self):
        if self._relay is not None:
            try:
                self._relay.off()
            except Exception:
                pass
        self._relay_on = False
        self.signals.on_relay(False)

    def is_relay_on(self):
        return self._relay_on

    def shutdown(self):
        if self._coin_reader is not None:
            self._coin_reader.stop()

    def available(self):
        # kept for compatibility: true when the coin reader is active
        return self.coin_reader_available()
