"""Persistent settings stored in settings.json (survives a reboot)."""

import json
import logging
import threading

import config

log = logging.getLogger("midiplayer.settings")

_lock = threading.Lock()

# Euro cents per song; selectable in the admin settings dialog.
PRICE_OPTIONS = (20, 50, 100, 150, 200)


def _read():
    try:
        with open(config.SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    try:
        with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log.error("settings opslaan faalde: %r", e)


def get_song_price_cents():
    with _lock:
        val = _read().get("song_price_cents", config.SONG_PRICE_CENTS)
    return val if val in PRICE_OPTIONS else config.SONG_PRICE_CENTS


def set_song_price_cents(cents):
    with _lock:
        data = _read()
        data["song_price_cents"] = cents
        _write(data)
