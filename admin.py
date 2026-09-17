"""Hidden admin access: PIN screen + settings dialog.

Triggered by pressing and holding the top-left corner of the kiosk for
3 seconds. After PIN 3082, an admin can rename song display names, delete
MIDI files, and edit MIDI metadata (title/artist/copyright/tempo) which is
written back into the .mid file via mido.
"""

import os
import logging

import config
import i18n

log = logging.getLogger("midiplayer.admin")

ADMIN_PIN = "3082"
HOLD_SECONDS = 3.0


def _qt():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QListWidget, QListWidgetItem, QMessageBox, QFormLayout, QSpinBox,
    )
    return Qt, QTimer, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, \
        QPushButton, QListWidget, QListWidgetItem, QMessageBox, QFormLayout, QSpinBox


class CornerHoldDetector:
    """Detects a 3-second hold in the top-left corner of a window."""

    def __init__(self, window, on_hold):
        self.window = window
        self.on_hold = on_hold
        self._timer = None
        self._active = False

    def press(self, pos):
        corner = min(self.window.width(), self.window.height()) // 4
        if pos.x() < corner and pos.y() < corner:
            self._active = True
            self._start()
        else:
            self._cancel()

    def release(self, pos):
        self._cancel()

    def _start(self):
        QTimer = _qt()[1]
        self._timer = QTimer(self.window)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._timer.start(int(HOLD_SECONDS * 1000))

    def _cancel(self):
        self._active = False
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None

    def _fire(self):
        if self._active:
            self.on_hold()
        self._cancel()


# --------------------------------------------------------------------------- #
# MIDI metadata read/write
# --------------------------------------------------------------------------- #
def read_midi_meta(path):
    """Return (title, artist, copyright, tempo_bpm, tracks, length)."""
    try:
        mido = __import__("mido")
        mid = mido.MidiFile(str(path))
    except Exception as e:
        log.error("read_midi_meta faalde voor %s: %r", path, e)
        return "", "", "", "", 0, 0.0

    title = artist = copyright = ""
    bpm = ""
    for track in mid.tracks:
        for msg in track:
            if msg.type == "track_name" and not title:
                title = msg.name
            elif msg.type == "text" and str(msg.text).startswith("Artist:"):
                artist = str(msg.text).split("Artist:", 1)[1].strip()
            elif msg.type == "copyright":
                copyright = msg.text
            elif msg.type == "set_tempo" and not bpm:
                bpm = f"{mido.tempo2bpm(msg.tempo):.0f}"
    try:
        length = float(mid.length)
    except Exception:
        length = 0.0
    return title, artist, copyright, bpm, len(mid.tracks), length


def write_midi_meta(path, title=None, artist=None, copyright=None, tempo_bpm=None):
    """Edit the in-memory SMF and write it back to the .mid file."""
    try:
        mido = __import__("mido")
        mid = mido.MidiFile(str(path))
    except Exception as e:
        log.error("write_midi_meta: open faalde voor %s: %r", path, e)
        return False

    first_track = mid.tracks[0] if mid.tracks else mido.MidiTrack()
    if not mid.tracks:
        mid.tracks.append(first_track)

    def _replace_or_insert(track, mtype, value, factory):
        for msg in list(track):
            if msg.type == mtype:
                if value:
                    new = factory(value)
                    idx = track.index(msg)
                    track[idx] = new
                else:
                    track.remove(msg)
                return
        if value:
            track.insert(0, factory(value))

    if title is not None:
        _replace_or_insert(first_track, "track_name", title,
                           lambda v: mido.MetaMessage("track_name", name=v))
    if copyright is not None:
        _replace_or_insert(first_track, "copyright", copyright,
                           lambda v: mido.MetaMessage("copyright", text=v))
    if artist is not None and artist:
        for msg in list(first_track):
            if msg.type == "text" and str(msg.text).startswith("Artist:"):
                first_track.remove(msg)
        first_track.insert(0, mido.MetaMessage("text", text=f"Artist: {artist}"))
    if tempo_bpm is not None:
        try:
            bpm_val = int(tempo_bpm)
            if bpm_val > 0:
                micros = mido.bpm2tempo(bpm_val)
                _replace_or_insert(first_track, "set_tempo", micros,
                                   lambda v: mido.MetaMessage("set_tempo", tempo=v))
        except Exception as e:
            log.error("write_midi_meta: tempo faalde: %r", e)

    try:
        mid.save(str(path))
        log.info("write_midi_meta: opgeslagen %s", path)
        return True
    except Exception as e:
        log.error("write_midi_meta: opslaan faalde voor %s: %r", path, e)
        return False


# --------------------------------------------------------------------------- #
# Dialog classes (built lazily so PySide6 is imported on demand)
# --------------------------------------------------------------------------- #
_PinDialog = None
_AdminSettingsDialog = None


def _build_pin_dialog():
    Qt, QTimer, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, \
        QPushButton = _qt()[:8]

    class _Impl(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(i18n.t("admin_pin_title"))
            self.setModal(True)
            self.resize(400, 220)
            layout = QVBoxLayout(self)
            prompt = QLabel(i18n.t("admin_pin_prompt"))
            prompt.setStyleSheet("font-size: 20px;")
            layout.addWidget(prompt)
            self.entry = QLineEdit()
            self.entry.setEchoMode(QLineEdit.Password)
            self.entry.setStyleSheet("font-size: 24px;")
            self.entry.returnPressed.connect(self._check)
            layout.addWidget(self.entry)
            self.msg = QLabel("")
            self.msg.setStyleSheet("color: #d93939; font-size: 16px;")
            layout.addWidget(self.msg)
            buttons = QHBoxLayout()
            ok = QPushButton(i18n.t("admin_save"))
            ok.setStyleSheet("font-size: 18px;")
            ok.clicked.connect(self._check)
            cancel = QPushButton(i18n.t("admin_close"))
            cancel.setStyleSheet("font-size: 18px;")
            cancel.clicked.connect(self.reject)
            buttons.addWidget(ok)
            buttons.addWidget(cancel)
            layout.addLayout(buttons)
            self.entry.setFocus()

        def _check(self):
            if self.entry.text() == ADMIN_PIN:
                self.accept()
            else:
                self.msg.setText(i18n.t("admin_pin_wrong"))
                self.entry.clear()

    return _Impl


def _build_settings_dialog():
    Qt, QTimer, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, \
        QPushButton, QListWidget, QListWidgetItem, QMessageBox, QFormLayout, \
        QSpinBox = _qt()

    import midi_library

    class _Impl(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(i18n.t("admin_settings_title"))
            self.setModal(True)
            self.resize(config.SCREEN_W - 80, config.SCREEN_H - 80)
            self._songs = []

            layout = QVBoxLayout(self)

            top = QHBoxLayout()
            title = QLabel(i18n.t("admin_settings_title"))
            title.setStyleSheet("font-size: 24px; font-weight: bold;")
            top.addWidget(title)
            top.addStretch(1)
            self.status_lbl = QLabel("")
            self.status_lbl.setStyleSheet("color: #39d98a; font-size: 16px;")
            top.addWidget(self.status_lbl)
            layout.addLayout(top)

            body = QHBoxLayout()

            left = QVBoxLayout()
            left.addWidget(QLabel(i18n.t("admin_song")))
            self.list_widget = QListWidget()
            self.list_widget.setStyleSheet("font-size: 18px;")
            self.list_widget.currentRowChanged.connect(self._on_select)
            left.addWidget(self.list_widget, 1)
            btns = QHBoxLayout()
            self.rename_btn = QPushButton(i18n.t("admin_rename"))
            self.rename_btn.setStyleSheet("font-size: 16px;")
            self.rename_btn.clicked.connect(self._rename)
            self.del_btn = QPushButton(i18n.t("admin_delete"))
            self.del_btn.setStyleSheet("font-size: 16px;")
            self.del_btn.clicked.connect(self._delete)
            btns.addWidget(self.rename_btn)
            btns.addWidget(self.del_btn)
            left.addLayout(btns)
            body.addLayout(left, 1)

            right = QVBoxLayout()
            right.addWidget(QLabel(i18n.t("admin_edit_meta")))
            form = QFormLayout()
            self.title_edit = QLineEdit()
            self.artist_edit = QLineEdit()
            self.copyright_edit = QLineEdit()
            self.tempo_edit = QSpinBox()
            self.tempo_edit.setRange(1, 400)
            for w in (self.title_edit, self.artist_edit, self.copyright_edit):
                w.setStyleSheet("font-size: 16px;")
            self.tempo_edit.setStyleSheet("font-size: 16px;")
            form.addRow(i18n.t("midi_title"), self.title_edit)
            form.addRow(i18n.t("midi_artist"), self.artist_edit)
            form.addRow(i18n.t("midi_copyright"), self.copyright_edit)
            form.addRow(i18n.t("midi_tempo"), self.tempo_edit)
            right.addLayout(form)

            save_btn = QPushButton(i18n.t("admin_save"))
            save_btn.setStyleSheet("font-size: 18px;")
            save_btn.clicked.connect(self._save_meta)
            right.addWidget(save_btn)
            right.addStretch(1)
            body.addLayout(right, 1)
            layout.addLayout(body, 1)

            close_btn = QPushButton(i18n.t("admin_close"))
            close_btn.setStyleSheet("font-size: 18px;")
            close_btn.clicked.connect(self.accept)
            layout.addWidget(close_btn)

            self._reload()

        def _reload(self):
            self._songs = midi_library.scan_midi()
            self.list_widget.clear()
            for s in self._songs:
                QListWidgetItem(s.name, self.list_widget)

        def _current_song(self):
            row = self.list_widget.currentRow()
            if 0 <= row < len(self._songs):
                return self._songs[row]
            return None

        def _on_select(self, row):
            song = self._current_song()
            if song is None:
                return
            title, artist, copyright, bpm, tracks, length = read_midi_meta(song.path)
            self.title_edit.setText(title or "")
            self.artist_edit.setText(artist or "")
            self.copyright_edit.setText(copyright or "")
            try:
                self.tempo_edit.setValue(int(bpm) if bpm else 120)
            except Exception:
                self.tempo_edit.setValue(120)

        def _rename(self):
            song = self._current_song()
            if song is None:
                return
            from pathlib import Path
            old = Path(song.path)
            dlg = QDialog(self)
            dlg.setWindowTitle(i18n.t("admin_rename"))
            dlg.setModal(True)
            v = QVBoxLayout(dlg)
            entry = QLineEdit(old.stem)
            entry.setStyleSheet("font-size: 18px;")
            v.addWidget(entry)
            h = QHBoxLayout()
            okbtn = QPushButton(i18n.t("admin_save"))
            cancelbtn = QPushButton(i18n.t("admin_close"))
            okbtn.clicked.connect(lambda: (setattr(dlg, "_name", entry.text()), dlg.accept()))
            cancelbtn.clicked.connect(dlg.reject)
            h.addWidget(okbtn)
            h.addWidget(cancelbtn)
            v.addLayout(h)
            if dlg.exec() == QDialog.Accepted and getattr(dlg, "_name", ""):
                new_stem = dlg._name.strip()
                if not new_stem:
                    return
                new_path = old.with_name(new_stem + old.suffix)
                if new_path == old:
                    return
                if new_path.exists():
                    QMessageBox.warning(self, i18n.t("admin_rename"), new_path.name)
                    return
                try:
                    os.rename(str(old), str(new_path))
                    self.status_lbl.setText(i18n.t("admin_saved"))
                    self._reload()
                except Exception as e:
                    QMessageBox.critical(self, i18n.t("admin_rename"), str(e))

        def _delete(self):
            song = self._current_song()
            if song is None:
                return
            if QMessageBox.question(self, i18n.t("admin_delete"),
                                    i18n.t("admin_confirm_delete")) != QMessageBox.StandardButton.Yes:
                return
            try:
                os.remove(song.path)
                self.status_lbl.setText(i18n.t("admin_deleted"))
                self._reload()
            except Exception as e:
                QMessageBox.critical(self, i18n.t("admin_delete"), str(e))

        def _save_meta(self):
            song = self._current_song()
            if song is None:
                return
            ok = write_midi_meta(
                song.path,
                title=self.title_edit.text().strip() or None,
                artist=self.artist_edit.text().strip() or None,
                copyright=self.copyright_edit.text().strip() or None,
                tempo_bpm=self.tempo_edit.value(),
            )
            self.status_lbl.setText(i18n.t("admin_saved") if ok else "Error")

    return _Impl


def _raise_dialog(dlg):
    """Bring a dialog above a fullscreen parent window."""
    from PySide6.QtCore import Qt as _Qt
    dlg.setWindowFlag(_Qt.WindowStaysOnTopHint, True)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def open_admin_flow(parent):
    global _PinDialog, _AdminSettingsDialog
    if _PinDialog is None:
        _PinDialog = _build_pin_dialog()
    if _AdminSettingsDialog is None:
        _AdminSettingsDialog = _build_settings_dialog()
    Qt, QTimer, QDialog = _qt()[:3]
    dlg = _PinDialog(parent)
    _raise_dialog(dlg)
    if dlg.exec() == QDialog.Accepted:
        settings = _AdminSettingsDialog(parent)
        _raise_dialog(settings)
        settings.exec()
