"""Scan the MIDI folder, sort alphabetically, parse SMF metadata."""

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


def scan_midi(folder=None):
    folder = Path(folder or config.MIDI_DIR)
    songs = []
    if folder.is_dir():
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".mid", ".midi"):
                songs.append(Song(path=str(p), name=p.stem))
    songs.sort(key=lambda s: s.name.lower())
    return songs


def find_midi_on_usb(root):
    root = Path(root)
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".mid", ".midi"):
            out.append(str(p))
    return out
