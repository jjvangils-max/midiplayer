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
import logging

import config

log = logging.getLogger("midiplayer.hardware")

# Import gpiozero's Button/OutputDevice independently of the LGPIOFactory:
# lgpio (Bookworm) is preferred but optional; if it is missing, gpiozero falls
# back to another factory and GPIO still works. Keeping them in one try would
# disable all GPIO just because the lgpio backend is absent.
try:
    from gpiozero import Button, OutputDevice
    _HAS_GPIO = True
except Exception as _e:
    _HAS_GPIO = False
    log.error("gpiozero import faalt (Button/OutputDevice niet beschikbaar): %r", _e)

try:
    from gpiozero.pins.lgpio import LGPIOFactory  # noqa: F401  (preferred on Bookworm)
    from gpiozero import Device
    if Device.pin_factory is None:
        Device.pin_factory = LGPIOFactory()
        log.info("gpiozero pin_factory ingesteld op LGPIOFactory")
except Exception as _e:
    log.warning("LGPIOFactory niet beschikbaar (lgpio ontbreekt?); gpiozero gebruikt een fallback-factory: %r", _e)

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

    def start(self):
        if not _HAS_GPIO:
            log.warning("gpiozero niet beschikbaar; GpioCoinReader uitgeschakeld")
            return
        try:
            from gpiozero import Device
            log.info("GpioCoinReader.start: pin=%s pull_up=%s active_high=%s factory=%s",
                     config.COIN_GPIO, config.COIN_PULL_UP, config.COIN_ACTIVE_HIGH,
                     getattr(Device, "pin_factory", None))
            self._button = Button(
                config.COIN_GPIO,
                pull_up=config.COIN_PULL_UP,
                bounce_time=config.COIN_DEBOUNCE,
            )
            log.info("GpioCoinReader: Button aangemaakt op BCM %s (rust is_active=%s), reader-thread start",
                     config.COIN_GPIO, self._button.is_active)
        except Exception as e:
            log.error("GpioCoinReader.start FAALT op BCM %s: %r", config.COIN_GPIO, e)
            self._button = None
            return
        threading.Thread.start(self)

    def stop(self):
        CoinReader.stop(self)
        if self._button is not None:
            try:
                self._button.close()
            except Exception:
                pass

    def available(self):
        return self._button is not None

    def run(self):
        """Poll the coin pin and count pulses per burst.

        gpiozero edge callbacks are unreliable for the JY-616's short (20 ms)
        pulses combined with bounce_time, so we poll button.is_active (a raw
        lgpio.gpio_read under the hood) and count pulses with a small software
        debounce state machine so contact bounce does not double-count. A burst
        is complete when no new pulse arrives for COIN_BURST_WINDOW after the
        last one.
        """
        window = config.COIN_BURST_WINDOW
        value_table = config.COIN_PULSE_VALUE
        debounce = max(0.005, config.COIN_DEBOUNCE)   # software debounce (s)
        poll = 0.01                                   # 10 ms sample interval
                                                   # (still < 20 ms pulse width)
        button = self._button
        pulses = 0
        last_pulse_time = 0.0
        armed = True            # ready to count a falling edge (pin at rest)
        high_since = None       # when the pin first returned to rest after a pulse
        prev = None             # previous pulse-state sample
        log.info("GpioCoinReader: polling gestart (rust is_active=%s)",
                 button.is_active)
        while not self._stop.is_set():
            time.sleep(poll)
            try:
                is_active = button.is_active
            except Exception as e:
                if not getattr(self, "_read_err_logged", False):
                    log.error("GpioCoinReader: fout bij lezen pin: %r", e)
                    self._read_err_logged = True
                continue
            # pull_up=True -> is_active is True when the pin is LOW. The coin
            # pulse (active_high=False) pulls the pin LOW, so the pulse state is
            # is_active; for an active-high pulse it is the inverse.
            pulse_now = (not is_active) if config.COIN_ACTIVE_HIGH else is_active
            now = time.monotonic()
            if prev is None:
                prev = pulse_now
                armed = not pulse_now
                continue
            if armed and pulse_now and not prev:
                # falling edge into the pulse state -> one pulse
                pulses += 1
                last_pulse_time = now
                armed = False
                high_since = None
                log.info("GpioCoinReader: puls %d gedetecteerd", pulses)
            elif not armed:
                # wait for the pin to return to rest and stay there (debounce)
                if not pulse_now:
                    if high_since is None:
                        high_since = now
                    elif now - high_since >= debounce:
                        armed = True
                else:
                    high_since = None
            prev = pulse_now
            if pulses and now - last_pulse_time >= window:
                cents = value_table.get(pulses)
                log.info("GpioCoinReader: burst compleet, %d puls(en) -> %s cent",
                         pulses, cents if cents is not None else "?")
                if cents:
                    self._report_coin(cents)
                else:
                    log.warning("GpioCoinReader: onbekend aantal pulsen: %d", pulses)
                pulses = 0


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
        charged = False
        with self._lock:
            if self.balance_cents >= price:
                self.balance_cents -= price
                charged = True
            balance = self.balance_cents
        # Always notify the UI with the current balance.
        self.signals.on_coin(balance)
        return charged

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
