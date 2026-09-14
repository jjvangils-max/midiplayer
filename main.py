"""Entrypoint: wire up hardware, MIDI player, state machine, GUI, USB monitor.

Run:  python main.py
"""

import sys
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
        self._pending_usb = None

    # hardware signals
    def on_coin(self, count):
        self.win.update_credits(count)
    def on_relay(self, on):
        self.win.update_status(
            i18n.t("relay_on") if on else i18n.t("relay_off")
        )

    # state machine signals
    def on_state_change(self, state, **info):
        if state == "playing":
            self.win.set_playing(True)
        elif state in ("stopped", "finished", "error", "idle"):
            self.win.set_playing(False)
    def on_status(self, text):
        self.win.update_status(text)
    def on_now_playing(self, name):
        self.win.update_now_playing(name)
    def on_queue_change(self, queue):
        self.win.update_queue(queue)

    # usb signals
    def on_usb_inserted(self, mount_path):
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG
        QMetaObject.invokeMethod(self.win, "_ask_usb_import",
                                 Qt.QueuedConnection, Q_ARG(str, mount_path))

    def on_import_progress(self, n_total, n_done):
        self.win.update_status(i18n.t("usb_importing", n=n_done))
    def on_import_done(self, n):
        self.win.update_status(i18n.t("usb_import_done", n=n))
        self.win.refresh_library()
    def on_import_error(self, msg):
        self.win.update_status(msg)


def main():
    i18n.set_language(config.DEFAULT_LANG)
    config.MIDI_DIR.mkdir(parents=True, exist_ok=True)

    app = build_app(sys.argv)
    win = MainWindow()
    win.show()

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
    sm = PlayerStateMachine(mp, hw, signals=_StateWiring())
    wiring.sm = sm

    class _HwWiring:
        on_coin = wiring.on_coin
        on_relay = wiring.on_relay
    hw.signals = _HwWiring()

    sm.start()

    # Wire GUI -> state machine
    win.signals.play_requested.connect(sm.request_play)
    win.signals.queue_requested.connect(sm.queue_next)
    win.signals.stop_requested.connect(sm.stop_now)

    # USB monitor (thread)
    class _UsbWiring:
        on_usb_inserted = wiring.on_usb_inserted
        on_import_progress = wiring.on_import_progress
        on_import_done = wiring.on_import_done
        on_import_error = wiring.on_import_error
    usb_mon = usb_import.UsbMonitor(signals=_UsbWiring())
    usb_mon.start()

    # install helper method on win to ask USB import (queued call target)
    win.usb_import_answer.connect(lambda yes, mount: (
        threading.Thread(
            target=lambda: usb_import.import_midi_from_usb(mount, signals=_UsbWiring()),
            daemon=True,
        ).start()
        if yes else None
    ))

    # initial status
    win.update_credits(hw.credits)
    if hw.available():
        win.update_status(i18n.t("insert_coin"))
    else:
        win.update_status(i18n.t("insert_coin") + "  (GPIO mock)")

    rc = app.exec()
    sm.shutdown()
    usb_mon.stop()
    hw.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
