"""Entrypoint: wire up hardware, MIDI player, state machine, GUI, USB monitor.

Run:  python main.py
"""

import sys
import logging
import threading

from PySide6.QtWidgets import QMessageBox

import config
import i18n
import hardware as hw_mod
import midi_player
import usb_import
from player_state import PlayerStateMachine
from main_window import MainWindow, build_app


class Wiring:
    """Glues callbacks from background threads to the GUI (thread-safe)."""

    def __init__(self, app, win, sm, hw):
        self.app = app
        self.win = win
        self.sm = sm
        self.hw = hw
        # Give the window direct access to hw/sm: _hide_welcome needs them to
        # decide whether to sleep after the welcome overlay disappears.
        self.win.hw = hw
        self.win.sm = sm
        self._pending_usb = None

    # hardware signals
    def on_coin(self, balance_cents):
        self.win.update_balance(balance_cents)
    def on_relay(self, on):
        self.win.set_status_key("relay_on" if on else "relay_off")
        self.win.set_screen_awake(on)
    def on_coin_value(self, cents):
        if not cents:
            return
        # The welcome overlay only appears when the coin wakes the kiosk
        # out of sleep (or the attract glow). Awake + interactive: credit only.
        self.win.on_coin_inserted()

    # state machine signals
    def on_state_change(self, state, **info):
        if state == "playing":
            self.win.set_playing(True)
        elif state in ("stopped", "finished", "error", "idle"):
            self.win.set_playing(False)
            self.win.stop_countdown()
    def on_status(self, key, **kwargs):
        self.win.set_status_key(key, **kwargs)
    def on_now_playing(self, name):
        self.win.update_now_playing(name)
    def on_queue_change(self, queue):
        self.win.update_queue(queue)
    def on_song_length(self, seconds):
        self.win.start_countdown(seconds)
    def on_song_progress(self, seconds):
        self.win.update_song_progress(seconds)
    def on_insufficient_credit(self, missing_cents):
        self.win.show_insufficient_credit(missing_cents)

    # usb signals
    def on_usb_inserted(self, mount_path):
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        QMetaObject.invokeMethod(self.win, "_ask_usb_import",
                                 Qt.QueuedConnection, Q_ARG(str, mount_path))

    def on_import_progress(self, n_total, n_done):
        self.win.set_status_key("usb_importing", n=n_done)
    def on_import_done(self, n):
        self.win.set_status_key("usb_import_done", n=n)
        self.win.refresh_library()
    def on_import_error(self, msg):
        self.win.update_status(msg)


def main():
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(levelname)s: %(message)s")
    i18n.set_language(config.DEFAULT_LANG)
    config.MIDI_DIR.mkdir(parents=True, exist_ok=True)

    app = build_app(sys.argv)
    win = MainWindow()
    win.showFullScreen()

    # Hardware (GPIO or mock)
    hw = hw_mod.Hardware()

    # MIDI player (thread)
    class _NoMidiSignals(midi_player.MidiPlayerSignals):
        pass
    mp = midi_player.MidiPlayer()

    # State machine (thread)
    wiring = Wiring(app, win, None, hw)

    class _StateWiring:
        on_state_change = wiring.on_state_change
        on_status = wiring.on_status
        on_now_playing = wiring.on_now_playing
        on_queue_change = wiring.on_queue_change
        on_song_length = wiring.on_song_length
        on_song_progress = wiring.on_song_progress
        on_insufficient_credit = wiring.on_insufficient_credit
    sm = PlayerStateMachine(mp, hw, signals=_StateWiring())
    wiring.sm = sm

    class _HwWiring:
        on_coin = wiring.on_coin
        on_relay = wiring.on_relay
        on_coin_value = wiring.on_coin_value
    hw.signals = _HwWiring()

    sm.start()

    # Wire GUI -> state machine
    win.signals.play_requested.connect(sm.request_play)

    # USB monitor (thread)
    class _UsbWiring:
        on_usb_inserted = wiring.on_usb_inserted
        on_import_progress = wiring.on_import_progress
        on_import_done = wiring.on_import_done
        on_import_error = wiring.on_import_error
    usb_mon = usb_import.UsbMonitor(signals=_UsbWiring())
    usb_mon.start()

    # install helper method on win to ask USB import (queued call target)
    win.signals.usb_import_answer.connect(lambda yes, mount: (
        threading.Thread(
            target=lambda: usb_import.import_midi_from_usb(mount, signals=_UsbWiring()),
            daemon=True,
        ).start()
        if yes else None
    ))

    # initial status
    win.update_balance(hw.balance_cents)
    if hw.available():
        win.set_status_key("insert_coin")
    else:
        win.update_status(i18n.t("insert_coin") + "  (GPIO mock)")

    # welcome overlay on startup
    win.show_welcome()

    rc = app.exec()
    sm.shutdown()
    usb_mon.stop()
    hw.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
