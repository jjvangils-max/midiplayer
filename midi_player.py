"""MIDI playback to both DIN outputs of the CME U4MIDI WC.

Uses rtmidi to open the ALSA output ports matching the configured names, then
plays the SMF note-for-note to all selected outputs simultaneously. Runs in its
own thread so the GUI stays responsive.
"""

import threading
import time

import config

import logging
log = logging.getLogger("midiplayer.midi")

try:
    import rtmidi  # python-rtmidi
    _HAS_RTMIDI = True
except Exception as _e:
    _HAS_RTMIDI = False
    log.error("rtmidi niet beschikbaar: %r", _e)

try:
    import mido
    _HAS_MIDO = True
except Exception as _e:
    _HAS_MIDO = False
    log.error("mido niet beschikbaar: %r", _e)


def list_ports():
    if not _HAS_RTMIDI:
        return []
    out = rtmidi.MidiOut(rtapi=rtmidi.API_LINUX_ALSA)
    ports = out.get_ports()
    out.close_port()
    del out
    return ports


def find_output_ports():
    """Return up to MIDI_NUM_OUTS ALSA port names matching the configured hints."""
    all_ports = list_ports()
    log.info("ALSA-poorten: %s", all_ports)
    matches = [p for p in all_ports if any(h in p for h in config.MIDI_OUT_PORT_NAMES)]
    log.info("CME-poorten gevonden: %s", matches)
    # Heuristic: physical DIN outs typically appear as separate ALSA clients.
    # If we have fewer matches than needed, just take what we have.
    return matches[: config.MIDI_NUM_OUTS] if matches else []


class MidiPlayerSignals:
    """Callbacks the GUI/state machine subscribes to (override these)."""
    def on_state(self, state): pass       # state: 'playing','stopped','finished','error'
    def on_progress(self, seconds): pass
    def on_error(self, msg): pass


class MidiPlayer(threading.Thread):
    def __init__(self, signals=None):
        super().__init__(daemon=True)
        self.signals = signals or MidiPlayerSignals()
        self._lock = threading.Lock()
        self._song = None          # path
        self._stop_flag = threading.Event()
        self._ports = []           # list of rtmidi.MidiOut
        self._open_ports()
        self.start()

    # ---- port management --------------------------------------------------
    def _open_ports(self):
        if not _HAS_RTMIDI:
            return
        names = find_output_ports()
        for n in names:
            try:
                mo = rtmidi.MidiOut(rtapi=rtmidi.API_LINUX_ALSA)
                mo.open_port(n)
                self._ports.append(mo)
                log.info("MIDI-uitgang geopend: %s", n)
            except Exception as e:
                log.error("Openen MIDI-poort %s faalde: %r", n, e)
        if not self._ports:
            log.warning("Geen CME-poort geopend; fallback op virtuele poort")
            # fall back: open a virtual port (no hardware) so the app still runs
            try:
                mo = rtmidi.MidiOut(rtapi=rtmidi.API_LINUX_ALSA)
                mo.open_virtual_port("MidiPlayer")
                self._ports.append(mo)
                log.info("Virtuele MIDI-poort geopend")
            except Exception as e:
                log.error("Virtuele poort faalde: %r", e)

    def available(self):
        return bool(self._ports)

    # ---- control ----------------------------------------------------------
    def play(self, song_path):
        with self._lock:
            self._song = song_path
            self._stop_flag.set()      # interrupt current
            self._stop_flag = threading.Event()
            self._go = True
            self._wake.set()

    def stop(self):
        self._stop_flag.set()
        self._all_notes_off()

    # ---- thread -----------------------------------------------------------
    def run(self):
        self._wake = threading.Event()
        self._go = False
        while True:
            self._wake.wait()
            self._wake.clear()
            if not self._go:
                continue
            song = self._song
            self._play_song(song)

    def _play_song(self, path):
        if not _HAS_MIDO or not self._ports:
            self.signals.on_error("MIDI backend not available")
            self.signals.on_state("error")
            return
        try:
            mid = mido.MidiFile(path)
        except Exception as e:
            self.signals.on_error(str(e))
            self.signals.on_state("error")
            return

        self._all_notes_off()
        self.signals.on_state("playing")
        start = time.time()
        for msg in mid:
            if self._stop_flag.is_set():
                break
            if msg.is_meta:
                continue
            if msg.time:
                self._sleep_scaled(msg.time)
            data = msg.bytes()
            if data:
                for port in self._ports:
                    try:
                        port.send_message(data)
                    except Exception:
                        pass
            self.signals.on_progress(time.time() - start)
        self._all_notes_off()
        if self._stop_flag.is_set():
            self.signals.on_state("stopped")
        else:
            self.signals.on_state("finished")

    def _sleep_scaled(self, dt):
        scaled = dt / config.TEMPO_SCALE
        end = time.time() + scaled
        while time.time() < end:
            if self._stop_flag.is_set():
                return
            time.sleep(min(0.01, end - time.time()))

    def _all_notes_off(self):
        for ch in range(16):
            msg = [0xB0 | ch, 123, 0]  # All Notes Off
            for port in self._ports:
                try:
                    port.send_message(msg)
                except Exception:
                    pass
