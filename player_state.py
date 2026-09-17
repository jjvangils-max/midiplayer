"""Playback state machine.

States:  IDLE -> WARMUP -> PLAYING -> GAP -> (PLAYING|COOLDOWN -> IDLE)

- When a song is requested and credits exist:
    * If relay already on and warmed up -> play immediately.
    * If relay off -> energise relay, show "Waiting for startup" for WARMUP_TIME,
      then play.
- After a song finishes -> wait GAP_TIME, then play next queued song if credits.
- If nothing is queued/playing -> start COOLDOWN timer (5 min); when it elapses,
  de-energise relay -> IDLE.
"""

import threading
import time
import logging

import config

log = logging.getLogger("midiplayer.state")


class StateSignals:
    def on_state_change(self, state, **info): pass
    def on_status(self, text): pass
    def on_now_playing(self, song_name): pass
    def on_queue_change(self, queue): pass


STATE_IDLE = "idle"
STATE_WARMUP = "warmup"
STATE_PLAYING = "playing"
STATE_GAP = "gap"
STATE_COOLDOWN = "cooldown"


class PlayerStateMachine(threading.Thread):
    def __init__(self, midi_player, hardware, signals=None):
        super().__init__(daemon=True)
        self.midi = midi_player
        self.hw = hardware
        self.signals = signals or StateSignals()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._queue = []          # list of song paths
        self._current = None
        self.state = STATE_IDLE
        self._cooldown_until = None
        self._warmup_until = None
        self._gap_until = None
        self._song_finished = threading.Event()

        # Subscribe to midi player
        class _M:
            def __init__(s, outer):
                s.outer = outer
            def on_state(s, state):
                if state == "finished":
                    s.outer._song_finished.set()
                s.outer.signals.on_state_change(state)
            def on_progress(s, seconds): pass
            def on_error(s, msg):
                s.outer.signals.on_status(msg)
        self.midi.signals = _M(self)

    # ---- external API -----------------------------------------------------
    def request_play(self, song_path):
        """User pressed PLAY for song_path. Requires a credit."""
        with self._lock:
            if not self.hw.consume_credit():
                self.signals.on_status("insert_coin")
                return False
            log.info("request_play: credits verbruikt, queue '%s'", song_path)
            self._queue.insert(0, song_path)
        self._wake.set()
        return True

    def queue_next(self, song_path):
        """Queue a song without consuming a credit (played after current)."""
        with self._lock:
            self._queue.append(song_path)
            self.signals.on_queue_change(list(self._queue))
        self._wake.set()

    def stop_now(self):
        self.midi.stop()
        with self._lock:
            self._queue.clear()
            self.signals.on_queue_change([])
        self._song_finished.set()
        self._wake.set()

    def shutdown(self):
        self._stop.set()
        self.midi.stop()
        self._wake.set()

    # ---- thread -----------------------------------------------------------
    def run(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            self._tick()

    def _tick(self):
        # Cooldown countdown
        if self.state == STATE_COOLDOWN:
            remaining = self._cooldown_until - time.time()
            if remaining <= 0:
                self.hw.relay_off()
                self._set_state(STATE_IDLE)
            else:
                self.signals.on_status("cooldown", s=int(remaining))
            return

        # If playing, wait for finish
        if self.state == STATE_PLAYING:
            if self._song_finished.is_set():
                self._song_finished.clear()
                self._current = None
                self.signals.on_now_playing("")
                self._start_gap()
            return

        if self.state == STATE_GAP:
            remaining = self._gap_until - time.time()
            if remaining <= 0:
                self._advance()
            else:
                self.signals.on_status("gap", s=int(remaining))
            return

        if self.state == STATE_WARMUP:
            remaining = self._warmup_until - time.time()
            if remaining <= 0:
                self._start_playing_first()
            else:
                self.signals.on_status("waiting_startup")
            return

        # IDLE: is there something to play with credit already consumed?
        if self.state == STATE_IDLE:
            with self._lock:
                q = list(self._queue)
            if q:
                self._begin_warmup_or_play()

    # ---- transitions ------------------------------------------------------
    def _begin_warmup_or_play(self):
        if not self.hw.is_relay_on():
            log.info("relay uit -> warmup %ss", config.WARMUP_TIME)
            self.hw.relay_on()
            self._warmup_until = time.time() + config.WARMUP_TIME
            self._set_state(STATE_WARMUP)
        else:
            log.info("relay aan -> direct afspelen")
            self._start_playing_first()

    def _start_playing_first(self):
        with self._lock:
            if not self._queue:
                self._set_state(STATE_IDLE)
                return
            song = self._queue.pop(0)
            self.signals.on_queue_change(list(self._queue))
        self._current = song
        self.signals.on_now_playing(Path_safe(song))
        self._song_finished.clear()
        log.info("Afspelen starten: %s", song)
        self.midi.play(song)
        self._set_state(STATE_PLAYING)

    def _start_gap(self):
        self._gap_until = time.time() + config.GAP_TIME
        self._set_state(STATE_GAP)

    def _advance(self):
        with self._lock:
            q = list(self._queue)
        if q:
            self._start_playing_first()
        else:
            self._cooldown_until = time.time() + config.COOLDOWN_TIME
            self._set_state(STATE_COOLDOWN)

    def _set_state(self, s):
        self.state = s
        log.info("State -> %s", s)
        info = {}
        if s == STATE_WARMUP:
            info["seconds"] = int((self._warmup_until - time.time()))
        self.signals.on_state_change(s, **info)
        self._wake.set()


def Path_safe(song):
    from pathlib import Path
    return Path(song).stem
