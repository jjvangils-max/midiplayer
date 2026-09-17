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
    """Return (index, name) pairs for up to MIDI_NUM_OUTS ALSA ports matching
    the configured hints. rtmidi's open_port() takes an integer index, not a
    name, so we return the index alongside the name for logging."""
    all_ports = list_ports()
    log.info("ALSA-poorten: %s", all_ports)
    matches = [(i, p) for i, p in enumerate(all_ports)
               if any(h in p for h in config.MIDI_OUT_PORT_NAMES)]
    log.info("CME-poorten gevonden: %s", [p for _, p in matches])
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
        matches = find_output_ports()
        for idx, name in matches:
            try:
                mo = rtmidi.MidiOut(rtapi=rtmidi.API_LINUX_ALSA)
                mo.open_port(idx)
                self._ports.append(mo)
                log.info("MIDI-uitgang geopend: %s (index %d)", name, idx)
            except Exception as e:
                log.error("Openen MIDI-poort %s (index %d) faalde: %r", name, idx, e)
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
        self._panic = True
        self._wake.set()  # break out of a sleep/wait promptly and panic

    # ---- thread -----------------------------------------------------------
    def run(self):
        self._wake = threading.Event()
        self._go = False
        self._panic = False
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._panic:
                self._panic = False
                self._all_notes_off()
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

        self.signals.on_state("playing")
        start = time.time()
        last_progress = 0.0
        was_stopped = False
        for msg in mid:
            if self._stop_flag.is_set():
                was_stopped = True
                break
            if msg.is_meta:
                continue
            if msg.time:
                self._sleep_scaled(msg.time)
            data = msg.bytes()
            if data:
                for port in self._ports:
                    self._send_safe(port, data)
            now = time.time()
            if now - last_progress >= 0.25:
                last_progress = now
                self.signals.on_progress(now - start)
        # Only run the full panic when the song was interrupted by stop().
        # On normal finish the SMF's own note-offs have silenced everything;
        # running the 2k-message panic on every song transition would delay
        # the start of the next song and make it miss its first bars.
        if was_stopped:
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

    def _send_safe(self, port, data):
        """Send a MIDI message, skipping invalid (out-of-range) bytes.

        Some MIDI files contain corrupt/out-of-range values that make
        rtmidi raise "data must be in range 0...127"; skip those rather than
        abort playback.
        """
        try:
            port.send_message(data)
        except Exception:
            pass

    def _all_notes_off(self):
        """Panic: turn off every note on every channel.

        CC 123/120 are ignored by many hardware synths (incl. the CME DIN
        outputs), so we send explicit Note-Off for all 128 notes on all 16
        channels — the only method that works universally. This is ~2k
        messages but completes in a few ms and is only sent on stop/finish.
        """
        for port in self._ports:
            for ch in range(16):
                # Sustain / sostenuto pedal off first, so pedal-held notes
                # are not shielded from the note-offs below.
                try:
                    port.send_message([0xB0 | ch, 64, 0])   # sustain off
                    port.send_message([0xB0 | ch, 66, 0])   # sostenuto off
                except Exception:
                    pass
                for note in range(128):
                    try:
                        port.send_message([0x80 | ch, note, 0])
                    except Exception:
                        pass
