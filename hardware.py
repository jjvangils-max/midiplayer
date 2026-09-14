"""Hardware layer: relay output + credit counting.

Coin input is handled by a pluggable "coin reader":
  * SerialCoinReader  -> JY-616 acceptor on a serial line (115200 baud, "4858xx")
  * GpioCoinReader     -> legacy pulse on a GPIO pin
  * Mock (no reader)  -> credits added programmatically (dev/test)

Falls back gracefully (no-op) when gpiozero / serial ports are unavailable so
the GUI still runs on a development machine. The interface stays identical.
"""

import threading

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
    def on_coin(self, count): pass        # new credit count
    def on_relay(self, on): pass           # True=on, False=off
    def on_coin_value(self, cents): pass  # value of the last coin in euro cents


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

    def _report_coin(self, value_code=None):
        """Register one credit. value_code is the JY-616 "xx" string if known."""
        with self.hw._lock:
            self.hw.credits += 1
            count = self.hw.credits
        self.hw.signals.on_coin(count)
        if value_code is not None:
            try:
                cents = int(value_code) * 10   # "10" -> 100 ct = 1 EUR
                self.hw.signals.on_coin_value(cents)
            except ValueError:
                pass


class SerialCoinReader(CoinReader, threading.Thread):
    """JY-616 serial acceptor.

    The device emits the prefix (default "4858") followed by a fixed number of
    value digits (default 2, e.g. "10"=1EUR) for each accepted coin. We buffer
    incoming bytes and emit one credit whenever a complete message is seen.
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
        prefix = config.COIN_SERIAL_PREFIX.encode("ascii", "ignore")
        ndig = config.COIN_VALUE_DIGITS
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
                msg_end = msg_start + ndig
                if len(buf) < msg_end:
                    # wait for more bytes
                    buf = buf[idx:]
                    break
                value_code = buf[msg_start:msg_end].decode("ascii", "ignore")
                self._report_coin(value_code)
                buf = buf[msg_end:]


class GpioCoinReader(CoinReader):
    """Legacy: coin acceptor that pulses a GPIO pin."""

    def __init__(self, hardware):
        super().__init__(hardware)
        self._button = None

    def start(self):
        if not _HAS_GPIO:
            return
        try:
            self._button = Button(
                config.COIN_GPIO,
                pull_up=config.COIN_PULL_UP,
                bounce_time=config.COIN_DEBOUNCE,
            )
            if config.COIN_ACTIVE_HIGH:
                self._button.when_activated = lambda: self._report_coin()
            else:
                self._button.when_deactivated = lambda: self._report_coin()
        except Exception:
            self._button = None

    def stop(self):
        CoinReader.stop(self)
        if self._button is not None:
            try:
                self._button.close()
            except Exception:
                pass

    def available(self):
        return self._button is not None


# ---------------------------------------------------------------------------
# Hardware: relay + credits + coin reader
# ---------------------------------------------------------------------------
class Hardware:
    def __init__(self, signals=None):
        self.signals = signals or HardwareSignals()
        self.credits = 0
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
    def add_credits(self, n=1):
        """Test hook: simulate coin(s) without hardware."""
        with self._lock:
            self.credits += n
            count = self.credits
        self.signals.on_coin(count)

    def consume_credit(self):
        with self._lock:
            if self.credits > 0:
                self.credits -= 1
                count = self.credits
                return True
            count = self.credits
        self.signals.on_coin(count)
        return False

    def set_credits(self, n):
        with self._lock:
            self.credits = max(0, n)
            count = self.credits
        self.signals.on_coin(count)

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
