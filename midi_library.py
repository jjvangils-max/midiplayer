"""Scan the MIDI folder, sort alphabetically, parse SMF metadata."""

import threading
from dataclasses import dataclass, field
from pathlib import Path

import config


def _load_meta(path):
    try:
        mido = __import__("mido")
        mid = mido.MidiFile(str(path))
    except Exception:
        return {"title": "", "artist": "", "copyright": "", "tempo": "", "tracks": 0, "length": 0.0}

    title = artist = copyright = ""
    bpm = ""
    for track in mid.tracks:
        for msg in track:
            if msg.type == "track_name" and not title:
                title = msg.name
            elif msg.type == "copyright":
                copyright = msg.text
            elif msg.type == "set_tempo" and not bpm:
                bpm = f"{mido.tempo2bpm(msg.tempo):.0f}"
    try:
        length = float(mid.length)
    except Exception:
        length = 0.0

    return {
        "title": title,
        "artist": artist,
        "copyright": copyright,
        "tempo": bpm,
        "tracks": len(mid.tracks),
        "length": length,
    }


@dataclass
class Song:
    path: str
    name: str          # display name (filename stem)
    title: str = ""
    artist: str = ""
    copyright: str = ""
    tempo: str = ""
    tracks: int = 0
    length: float = 0.0
    meta_loaded: bool = field(default=False)

    def load_meta(self):
        if self.meta_loaded:
            return
        meta = _load_meta(self.path)
        self.title = meta["title"]
        self.artist = meta["artist"]
        self.copyright = meta["copyright"]
        self.tempo = meta["tempo"]
        self.tracks = meta["tracks"]
        self.length = meta["length"]
        self.meta_loaded = True


def _is_midi_file(p):
    """True if p is a MIDI file we want to use, skipping macOS/hidden junk.

    Skips dotfiles and the AppleDouble metadata files (._*) that macOS writes
    on FAT/exFAT USB sticks; those are not real MIDI files.
    """
    name = p.name
    if name.startswith(".") or name.startswith("._"):
        return False
    return p.suffix.lower() in (".mid", ".midi")


def scan_midi(folder=None):
    folder = Path(folder or config.MIDI_DIR)
    songs = []
    if folder.is_dir():
        for p in folder.rglob("*"):
            if p.is_file() and _is_midi_file(p):
                songs.append(Song(path=str(p), name=p.stem))
    songs.sort(key=lambda s: s.name.lower())
    return songs


def preload_meta(songs, on_progress=None):
    """Parse metadata for all songs in the background (thread-safe).

    Call from a daemon thread: load_meta() sets attributes on each Song; the
    GUI thread reads the already-cached values via load_meta() (a no-op once
    meta_loaded). This avoids parsing MIDI files on the GUI thread, which was
    the cause of the slow song-info / page-switch stutter.
    """
    n = len(songs)
    for i, s in enumerate(songs):
        s.load_meta()
        if on_progress is not None:
            try:
                on_progress(i + 1, n)
            except Exception:
                pass


def start_preload_thread(songs, on_progress=None):
    t = threading.Thread(target=preload_meta, args=(songs, on_progress),
                         daemon=True)
    t.start()
    return t


def find_midi_on_usb(root):
    root = Path(root)
    out = []
    for p in root.rglob("*"):
        if p.is_file() and _is_midi_file(p):
            out.append(str(p))
    return out
