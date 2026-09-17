"""Qt GUI for the MIDI player. Fullscreen 1024x600, large touch buttons.

Layout:
  +----------------------------------------------------------+
  | Title            Credits: N            [🇬🇧 EN] [🇫🇷 FR]   |  top bar
  +-------------------+--------------------------------------+
  |  Song list (8     |  Information panel                    |
  |  big buttons,    |  (title/artist/copyright/tempo/       |
  |  2 x 4 grid)     |   length/tracks)                      |
  |                   |                                       |
  |                   |  Now playing: ...                     |
  |                   |  Up next: ...                         |
  |  [◀]  Page 1/3   |                                       |
  |  [▶]              |  [      PLAY      ]  [    STOP    ]   |
  +-------------------+--------------------------------------+
  | status bar: "Waiting for startup…"  /  relay state        |
  +----------------------------------------------------------+
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, Slot, QMetaObject, Q_ARG
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QGridLayout,
    QVBoxLayout, QHBoxLayout, QTextEdit, QFrame, QSizePolicy,
)

import config
import i18n
import midi_library


STYLE = """
QWidget { background: #101820; color: #e8eef2; font-family: 'DejaVu Sans'; }
QPushButton {
  background: #1f2d3a; border: 2px solid #33506b; border-radius: 12px;
  color: #e8eef2; padding: 10px;
}
QPushButton:pressed { background: #2d4a63; }
QPushButton#songBtn { background: #16273a; }
QPushButton#songBtn:selected,
QPushButton#songBtn:checked { background: #1f5f8f; border-color: #4aa3df; }
QPushButton#playBtn { background: #1b7a3d; border: 3px solid #39d98a; font-weight: bold; color: white; }
QPushButton#playBtn:disabled { background: #2a3a30; color: #6a7a70; border-color: #33504a; }
QPushButton#stopBtn { background: #7a1b1b; border: 3px solid #d93939; font-weight: bold; color: white; }
QPushButton#navBtn { background: #22303d; }
QPushButton#flagBtn { background: #16202a; border: 1px solid #2a3a4a; }
QLabel#title { font-size: 26px; font-weight: bold; }
QLabel#credits { font-size: 22px; font-weight: bold; color: #ffd166; }
QLabel#status { font-size: 20px; font-weight: bold; color: #4aa3df; }
QTextEdit { background: #0c1419; border: 2px solid #2a3a4a; border-radius: 10px;
            font-size: 18px; padding: 8px; }
QLabel#section { font-size: 16px; color: #88a0b0; }
QLabel#nowplaying { font-size: 20px; font-weight: bold; color: #39d98a; }
"""


def _btn_font(pt):
    f = QFont(); f.setPointSize(pt); f.setBold(True); return f


class WindowSignals(QObject):
    play_requested = Signal(str)        # song path
    queue_requested = Signal(str)       # song path
    stop_requested = Signal()
    language_changed = Signal(str)     # 'en' or 'fr'
    usb_import_answer = Signal(bool, str)  # yes/no, mount path


class MainWindow(QMainWindow):
    def __init__(self, state_machine=None, hw=None):
        super().__init__()
        self.signals = WindowSignals()
        self.sm = state_machine
        self.hw = hw
        self.songs = []
        self.page = 0
        self.selected_index = -1
        self._now_name = ""
        self._queue_text = ""
        self._status_key = "insert_coin"
        self._status_kwargs = {}
        self._init_ui()
        self.refresh_library()
        self._hold_detector = None
        # Hidden admin access: hold top-left corner 3s -> PIN -> settings.
        import admin
        self._hold_detector = admin.CornerHoldDetector(
            self, lambda: admin.open_admin_flow(self))
        # Periodically refresh the info panel so metadata filled by the
        # background preload appears without re-parsing on the GUI thread.
        from PySide6.QtCore import QTimer
        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self._maybe_refresh_info)
        self._info_timer.start(500)

    def _maybe_refresh_info(self):
        # Re-render the info panel if the selected song's metadata just became
        # available from the background preload.
        if 0 <= self.selected_index < len(self.songs):
            s = self.songs[self.selected_index]
            if s.meta_loaded and not getattr(self, "_info_rendered_for", None) == s.path:
                self._info_rendered_for = s.path
                self._update_info()

    # ---- UI ---------------------------------------------------------------
    def _init_ui(self):
        self.setWindowTitle(i18n.t("title"))
        self.resize(config.SCREEN_W, config.SCREEN_H)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addLayout(self._top_bar())

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addLayout(self._left_panel(), 1)
        body.addLayout(self._right_panel(), 1)
        root.addLayout(body, 1)

        root.addWidget(self._status_bar())

    def _top_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.title_lbl = QLabel(i18n.t("title"))
        self.title_lbl.setObjectName("title")
        self.balance_lbl = QLabel(i18n.t("balance", amount="0.00"))
        self.balance_lbl.setObjectName("credits")
        self.balance_lbl.setAlignment(Qt.AlignCenter)
        bar.addWidget(self.title_lbl)
        bar.addStretch(1)
        bar.addWidget(self.balance_lbl)
        bar.addStretch(1)

        self.flag_en = QPushButton("🇬🇧 EN")
        self.flag_fr = QPushButton("🇫🇷 FR")
        for b in (self.flag_en, self.flag_fr):
            b.setObjectName("flagBtn")
            b.setFixedHeight(52)
            b.setFixedWidth(110)
            b.setFont(_btn_font(14))
        self.flag_en.clicked.connect(lambda: self._set_language("en"))
        self.flag_fr.clicked.connect(lambda: self._set_language("fr"))
        bar.addWidget(self.flag_en)
        bar.addWidget(self.flag_fr)
        return bar

    def _left_panel(self):
        col = QVBoxLayout()
        col.setSpacing(6)
        self.song_grid = QGridLayout()
        self.song_grid.setSpacing(6)
        self.song_buttons = []
        for i in range(config.SONGS_PER_PAGE):
            r, c = divmod(i, 2)  # 2 columns x 4 rows
            b = QPushButton("")
            b.setObjectName("songBtn")
            b.setCheckable(True)
            b.setFont(_btn_font(16))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            b.setMinimumHeight(88)
            b.clicked.connect(lambda _, idx=i: self._select_visible(idx))
            self.song_grid.addWidget(b, r, c)
            self.song_buttons.append(b)
        col.addLayout(self.song_grid, 1)

        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.prev_btn = QPushButton(i18n.t("prev"))
        self.prev_btn.setObjectName("navBtn")
        self.prev_btn.setFont(_btn_font(20))
        self.prev_btn.setFixedHeight(56)
        self.prev_btn.clicked.connect(self._prev_page)
        self.page_lbl = QLabel("1/1")
        self.page_lbl.setObjectName("section")
        self.page_lbl.setAlignment(Qt.AlignCenter)
        self.page_lbl.setFont(_btn_font(14))
        self.next_btn = QPushButton(i18n.t("next"))
        self.next_btn.setObjectName("navBtn")
        self.next_btn.setFont(_btn_font(20))
        self.next_btn.setFixedHeight(56)
        self.next_btn.clicked.connect(self._next_page)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_lbl, 1)
        nav.addWidget(self.next_btn)
        col.addLayout(nav)
        return col

    def _right_panel(self):
        col = QVBoxLayout()
        col.setSpacing(8)

        sec = QLabel(i18n.t("info"))
        sec.setObjectName("section")
        col.addWidget(sec)

        self.info_edit = QTextEdit()
        self.info_edit.setReadOnly(True)
        self.info_edit.setFont(_btn_font(16))
        col.addWidget(self.info_edit, 2)

        self.now_lbl = QLabel(i18n.t("now_playing") + " " + i18n.t("queue_empty"))
        self.now_lbl.setObjectName("nowplaying")
        self.now_lbl.setWordWrap(True)
        self.now_lbl.setFont(_btn_font(16))
        col.addWidget(self.now_lbl)

        self.next_lbl = QLabel(i18n.t("up_next") + " " + i18n.t("queue_empty"))
        self.next_lbl.setObjectName("nowplaying")
        self.next_lbl.setWordWrap(True)
        self.next_lbl.setFont(_btn_font(14))
        col.addWidget(self.next_lbl)

        ctrls = QHBoxLayout()
        ctrls.setSpacing(10)
        self.play_btn = QPushButton(i18n.t("play"))
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setFont(_btn_font(28))
        self.play_btn.setMinimumHeight(110)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._on_play)
        self.stop_btn = QPushButton(i18n.t("stop"))
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setFont(_btn_font(22))
        self.stop_btn.setMinimumHeight(110)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        ctrls.addWidget(self.play_btn, 3)
        ctrls.addWidget(self.stop_btn, 2)
        col.addLayout(ctrls)

        self.queue_btn = QPushButton(i18n.t("add_to_queue"))
        self.queue_btn.setFont(_btn_font(16))
        self.queue_btn.setMinimumHeight(64)
        self.queue_btn.setEnabled(False)
        self.queue_btn.clicked.connect(self._on_queue)
        col.addWidget(self.queue_btn)
        return col

    def _status_bar(self):
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("status")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setFont(_btn_font(16))
        self.status_lbl.setMinimumHeight(40)
        self.status_lbl.setFrameShape(QFrame.StyledPanel)
        return self.status_lbl

    # ---- library / pages -------------------------------------------------
    def refresh_library(self):
        # may be called from a background thread (USB import); marshal to GUI
        QMetaObject.invokeMethod(self, "_slot_refresh_library", Qt.QueuedConnection)

    @Slot()
    def _slot_refresh_library(self):
        self.songs = midi_library.scan_midi()
        self.page = 0
        self._render_page()
        # Pre-parse metadata in the background so selecting a song / switching
        # pages never blocks the GUI thread on a file-system read.
        midi_library.start_preload_thread(self.songs)

    def _render_page(self):
        per = config.SONGS_PER_PAGE
        total = len(self.songs)
        pages = max(1, (total + per - 1) // per)
        self.page_lbl.setText(i18n.t("page", p=self.page + 1, n=pages))
        start = self.page * per
        for i, b in enumerate(self.song_buttons):
            idx = start + i
            if idx < total:
                b.setText(self.songs[idx].name)
                b.setEnabled(True)
            else:
                b.setText("")
                b.setEnabled(False)
                b.setChecked(False)
        self.selected_index = -1
        self._update_info()
        self._update_play_enabled()

    def _prev_page(self):
        per = config.SONGS_PER_PAGE
        pages = max(1, (len(self.songs) + per - 1) // per)
        if self.page > 0:
            self.page -= 1
        else:
            self.page = pages - 1
        self._render_page()

    def _next_page(self):
        per = config.SONGS_PER_PAGE
        pages = max(1, (len(self.songs) + per - 1) // per)
        if self.page < pages - 1:
            self.page += 1
        else:
            self.page = 0
        self._render_page()

    def _select_visible(self, visible_idx):
        idx = self.page * config.SONGS_PER_PAGE + visible_idx
        if idx >= len(self.songs):
            return
        self.selected_index = idx
        self._info_rendered_for = None
        for i, b in enumerate(self.song_buttons):
            b.setChecked(i == visible_idx)
        self._update_info()
        self._update_play_enabled()

    def _update_info(self):
        if 0 <= self.selected_index < len(self.songs):
            s = self.songs[self.selected_index]
            if not s.meta_loaded:
                # Metadata not parsed yet (background preload still running).
                # Show a placeholder and let the preload thread fill it; do
                # NOT parse on the GUI thread (that was the stutter cause).
                self.info_edit.setPlainText(i18n.t("info") + "…")
                return
            rows = [
                f"{i18n.t('midi_title')}: {s.title or i18n.t('midi_unknown')}",
                f"{i18n.t('midi_artist')}: {s.artist or i18n.t('midi_unknown')}",
                f"{i18n.t('midi_copyright')}: {s.copyright or i18n.t('midi_unknown')}",
                f"{i18n.t('midi_tempo')}: {s.tempo or i18n.t('midi_unknown')} BPM",
                f"{i18n.t('midi_length')}: {int(s.length//60):02d}:{int(s.length%60):02d}",
                f"{i18n.t('midi_tracks')}: {s.tracks}",
            ]
            self.info_edit.setPlainText("\n".join(rows))
        else:
            self.info_edit.setPlainText(i18n.t("no_song_selected"))

    def _update_play_enabled(self):
        has_sel = 0 <= self.selected_index < len(self.songs)
        self.play_btn.setEnabled(has_sel)
        self.queue_btn.setEnabled(has_sel)

    # ---- actions ----------------------------------------------------------
    def _on_play(self):
        if 0 <= self.selected_index < len(self.songs):
            self.signals.play_requested.emit(self.songs[self.selected_index].path)

    def _on_queue(self):
        if 0 <= self.selected_index < len(self.songs):
            self.signals.queue_requested.emit(self.songs[self.selected_index].path)

    def _on_stop(self):
        self.signals.stop_requested.emit()

    # ---- language ---------------------------------------------------------
    def _set_language(self, lang):
        i18n.set_language(lang)
        self.setWindowTitle(i18n.t("title"))
        self.title_lbl.setText(i18n.t("title"))
        self.prev_btn.setText(i18n.t("prev"))
        self.next_btn.setText(i18n.t("next"))
        self.play_btn.setText(i18n.t("play"))
        self.stop_btn.setText(i18n.t("stop"))
        self.queue_btn.setText(i18n.t("add_to_queue"))
        # section labels re-render
        self._render_page()
        self._update_info()
        self._update_balance(self.hw.balance_cents if self.hw else 0)
        self._render_now()
        self._render_next()
        if self._status_key is not None:
            self._render_status()
        self.signals.language_changed.emit(lang)

    # ---- slots from state machine / hardware (thread-safe via signals) ----
    def update_balance(self, cents):
        QMetaObject.invokeMethod(self, "_slot_balance", Qt.QueuedConnection,
                                 Q_ARG(int, cents))

    def update_status(self, text):
        QMetaObject.invokeMethod(self, "_slot_status", Qt.QueuedConnection,
                                 Q_ARG(str, text))

    def set_status_key(self, key, **kwargs):
        # may be called from a background thread; marshal to the GUI thread
        self._status_key = key
        self._status_kwargs = kwargs
        QMetaObject.invokeMethod(self, "_slot_status_key", Qt.QueuedConnection)

    @Slot()
    def _slot_status_key(self):
        self._render_status()

    def _render_status(self):
        text = i18n.t(self._status_key, **self._status_kwargs)
        self.status_lbl.setText(text)

    def update_now_playing(self, name):
        QMetaObject.invokeMethod(self, "_slot_now", Qt.QueuedConnection,
                                 Q_ARG(str, name))

    def update_queue(self, queue):
        text = "  ·  ".join(Path(p).stem for p in queue)
        QMetaObject.invokeMethod(self, "_slot_queue", Qt.QueuedConnection,
                                 Q_ARG(str, text))

    def set_playing(self, playing):
        QMetaObject.invokeMethod(self, "_slot_playing", Qt.QueuedConnection,
                                 Q_ARG(bool, playing))

    # ---- actual slot implementations (invoked on GUI thread) --------------
    @Slot(int)
    def _slot_balance(self, cents):
        self._update_balance(cents)

    def _update_balance(self, cents):
        amount = f"{cents / 100:.2f}"
        self.balance_lbl.setText(i18n.t("balance", amount=amount))

    @Slot(str)
    def _slot_status(self, text):
        # plain-text status (errors): clear the keyed status so a language
        # switch does not overwrite it with a stale translated key.
        self._status_key = None
        self._status_kwargs = {}
        self.status_lbl.setText(text)

    @Slot(str)
    def _slot_now(self, name):
        self._now_name = name
        self._render_now()

    @Slot(str)
    def _slot_queue(self, text):
        self._queue_text = text
        self._render_next()

    def _render_now(self):
        if self._now_name:
            self.now_lbl.setText(i18n.t("now_playing") + " " + self._now_name)
        else:
            self.now_lbl.setText(i18n.t("now_playing") + " " + i18n.t("queue_empty"))

    def _render_next(self):
        if self._queue_text:
            self.next_lbl.setText(i18n.t("up_next") + " " + self._queue_text)
        else:
            self.next_lbl.setText(i18n.t("up_next") + " " + i18n.t("queue_empty"))

    @Slot(bool)
    def _slot_playing(self, playing):
        self.stop_btn.setEnabled(playing)

    # ---- USB import dialog (invoked via queued meta call) -----------------
    @Slot(str)
    def _ask_usb_import(self, mount_path):
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle(i18n.t("usb_detected"))
        msg.setText(i18n.t("usb_import_question"))
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.button(QMessageBox.Yes).setText(i18n.t("usb_import_yes"))
        msg.button(QMessageBox.No).setText(i18n.t("usb_import_no"))
        if msg.exec() == QMessageBox.Yes:
            self.signals.usb_import_answer.emit(True, mount_path)

    # ---- hidden admin corner-hold ----------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._hold_detector is not None:
            self._hold_detector.press(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_detector is not None:
            self._hold_detector.release(event.position().toPoint())
        super().mouseReleaseEvent(event)


def build_app(argv):
    app = QApplication(argv)
    app.setStyleSheet(STYLE)
    return app
