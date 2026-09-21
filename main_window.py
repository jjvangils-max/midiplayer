"""Qt GUI for the MIDI player. Fullscreen 1024x600, large touch buttons.

Layout:
  +----------------------------------------------------------+
  | Title            Balance: 0.00       [🇬🇧 EN] [🇫🇷 FR]   |  top bar
  |  Song list (9 big buttons, 3 x 3 grid)                   |
  |                                                           |
  |  [◀]  Page 1/3 [▶]                    [      PLAY      ]  |
  |  Now playing: ...                Up next: ...             |
  +----------------------------------------------------------+
  | status bar: countdown / relay state                       |
  +----------------------------------------------------------+

The screen dims to near-black when the installation powers down (relay off)
and wakes with a French welcome message when a coin is inserted.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, Slot, QMetaObject, Q_ARG, QTimer
from PySide6.QtGui import QFont, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QGridLayout,
    QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
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
        self._last_balance_cents = 0
        self._awake = True
        self._dim_overlay = None
        self._welcome_overlay = None
        self._init_ui()
        self.refresh_library()
        self._hold_detector = None
        # Hidden admin access: hold the app title 5s -> PIN -> settings.
        import admin
        self._hold_detector = admin.CornerHoldDetector(
            self, lambda: admin.open_admin_flow(self))
        self.title_lbl.installEventFilter(self)
        # Periodically refresh the info panel so metadata filled by the
        # background preload appears without re-parsing on the GUI thread.
        from PySide6.QtCore import QTimer
        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self._maybe_refresh_info)
        self._info_timer.start(500)
        # Song countdown in the status bar while a song is playing.
        self._song_total = 0.0
        self._song_elapsed = 0.0
        self._countdown_active = False
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._tick_countdown)
        self._countdown_timer.start(1000)
        # Screen sleep: wake overlay with French welcome on a coin insert.
        self._welcome_timer = QTimer(self)
        self._welcome_timer.setSingleShot(True)
        self._welcome_timer.timeout.connect(self._hide_welcome)

    def _maybe_refresh_info(self):
        # Once the background preload has checked all songs, drop any corrupt
        # ones from the list and re-render the page. Do this only once per
        # refresh cycle.
        if all(getattr(s, "meta_checked", False) for s in self.songs):
            valid = [s for s in self.songs if getattr(s, "valid", True)]
            if len(valid) != len(self.songs):
                self.songs = valid
                self.selected_index = -1
                self._render_page()

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
        root.addLayout(self._song_panel(), 1)
        root.addLayout(self._nav_row())
        root.addWidget(self._now_row())
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

    def _song_panel(self):
        col = QVBoxLayout()
        col.setSpacing(6)
        self.song_grid = QGridLayout()
        self.song_grid.setSpacing(6)
        self.song_buttons = []
        for i in range(config.SONGS_PER_PAGE):
            r, c = divmod(i, 3)  # 3 columns x 4 rows
            b = QPushButton("")
            b.setObjectName("songBtn")
            b.setCheckable(True)
            b.setFont(_btn_font(16))
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            b.setMinimumHeight(78)
            b.clicked.connect(lambda _, idx=i: self._select_visible(idx))
            self.song_grid.addWidget(b, r, c)
            self.song_buttons.append(b)
        col.addLayout(self.song_grid, 1)
        return col

    def _nav_row(self):
        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.prev_btn = QPushButton(i18n.t("prev"))
        self.prev_btn.setObjectName("navBtn")
        self.prev_btn.setFont(_btn_font(20))
        self.prev_btn.setFixedHeight(64)
        self.prev_btn.clicked.connect(self._prev_page)
        self.page_lbl = QLabel("1/1")
        self.page_lbl.setObjectName("section")
        self.page_lbl.setAlignment(Qt.AlignCenter)
        self.page_lbl.setFont(_btn_font(14))
        self.next_btn = QPushButton(i18n.t("next"))
        self.next_btn.setObjectName("navBtn")
        self.next_btn.setFont(_btn_font(20))
        self.next_btn.setFixedHeight(64)
        self.next_btn.clicked.connect(self._next_page)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_lbl, 1)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        self.hint_lbl = QLabel(i18n.t("play_hint"))
        self.hint_lbl.setObjectName("section")
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setFont(_btn_font(12))
        self.hint_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nav.addWidget(self.hint_lbl, 1)
        self.play_btn = QPushButton(i18n.t("play"))
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setFont(_btn_font(28))
        self.play_btn.setFixedHeight(64)
        self.play_btn.setMinimumWidth(260)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._on_play)
        nav.addWidget(self.play_btn)
        return nav

    def _now_row(self):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.now_lbl = QLabel(i18n.t("now_playing") + " " + i18n.t("queue_empty"))
        self.now_lbl.setObjectName("nowplaying")
        self.now_lbl.setWordWrap(True)
        self.now_lbl.setFont(_btn_font(16))
        self.next_lbl = QLabel(i18n.t("up_next") + " " + i18n.t("queue_empty"))
        self.next_lbl.setObjectName("nowplaying")
        self.next_lbl.setWordWrap(True)
        self.next_lbl.setFont(_btn_font(14))
        h.addWidget(self.now_lbl, 1)
        h.addWidget(self.next_lbl, 1)
        return row

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
        for i, b in enumerate(self.song_buttons):
            b.setChecked(i == visible_idx)
        self._update_play_enabled()

    def _update_play_enabled(self):
        has_sel = 0 <= self.selected_index < len(self.songs)
        self.play_btn.setEnabled(has_sel)

    # ---- actions ----------------------------------------------------------
    def _on_play(self):
        if 0 <= self.selected_index < len(self.songs):
            self.signals.play_requested.emit(self.songs[self.selected_index].path)

    # ---- language ---------------------------------------------------------
    def _set_language(self, lang):
        i18n.set_language(lang)
        self.setWindowTitle(i18n.t("title"))
        self.title_lbl.setText(i18n.t("title"))
        self.prev_btn.setText(i18n.t("prev"))
        self.next_btn.setText(i18n.t("next"))
        self.play_btn.setText(i18n.t("play"))
        self.hint_lbl.setText(i18n.t("play_hint"))
        # section labels re-render
        self._render_page()
        self._update_balance(getattr(self, "_last_balance_cents", 0))
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
        self._last_balance_cents = cents
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
        pass

    # ---- screen sleep / wake ------------------------------------------------
    def set_screen_awake(self, awake):
        QMetaObject.invokeMethod(self, "_slot_screen_awake", Qt.QueuedConnection,
                                 Q_ARG(bool, bool(awake)))

    @Slot(bool)
    def _slot_screen_awake(self, awake):
        if awake == self._awake:
            return
        self._awake = awake
        if awake:
            self._remove_dim()
        else:
            self._apply_dim()

    def _apply_dim(self):
        if getattr(self, "_dim_overlay", None) is not None:
            return
        from PySide6.QtWidgets import QWidget as _W
        overlay = _W(self)
        overlay.setObjectName("dimOverlay")
        overlay.setStyleSheet("background: #000000;")
        overlay.setGeometry(self.rect())
        overlay.show()
        overlay.raise_()
        self._dim_overlay = overlay

    def _remove_dim(self):
        ov = getattr(self, "_dim_overlay", None)
        if ov is not None:
            ov.deleteLater()
            self._dim_overlay = None

    def resizeEvent(self, event):
        ov = getattr(self, "_dim_overlay", None)
        if ov is not None:
            ov.setGeometry(self.rect())
        super().resizeEvent(event)

    def show_welcome(self):
        QMetaObject.invokeMethod(self, "_slot_show_welcome", Qt.QueuedConnection)

    @Slot()
    def _slot_show_welcome(self):
        self._slot_screen_awake(True)
        if self._welcome_overlay is not None:
            self._welcome_timer.stop()
            self._welcome_overlay.deleteLater()
        from PySide6.QtWidgets import QWidget as _W
        overlay = _W(self)
        overlay.setStyleSheet("background: #000000;")
        overlay.setGeometry(self.rect())
        lay = QVBoxLayout(overlay)
        lay.addStretch(1)
        msg = QLabel(i18n.t("welcome"))
        msg.setStyleSheet("font-size: 44px; font-weight: bold; color: #ffd166;"
                          "background: transparent;")
        msg.setAlignment(Qt.AlignCenter)
        lay.addWidget(msg)
        sub = QLabel(i18n.t("welcome_sub"))
        sub.setStyleSheet("font-size: 24px; color: #e8eef2;"
                          "background: transparent;")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addStretch(1)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        self._welcome_overlay = overlay
        self._welcome_timer.start(int(getattr(config, "WELCOME_TIME", 3)) * 1000)

    @Slot()
    def _hide_welcome(self):
        if self._welcome_overlay is not None:
            self._welcome_overlay.deleteLater()
            self._welcome_overlay = None

    # ---- song countdown in the status bar --------------------------------
    def start_countdown(self, total_seconds):
        QMetaObject.invokeMethod(self, "_slot_start_countdown",
                                  Qt.QueuedConnection, Q_ARG(float, float(total_seconds)))

    @Slot(float)
    def _slot_start_countdown(self, total_seconds):
        self._song_total = max(0.0, total_seconds)
        self._song_elapsed = 0.0
        self._countdown_active = self._song_total > 0
        if self._countdown_active:
            self._render_countdown()

    def stop_countdown(self):
        QMetaObject.invokeMethod(self, "_slot_stop_countdown", Qt.QueuedConnection)

    @Slot()
    def _slot_stop_countdown(self):
        self._countdown_active = False

    def update_song_progress(self, elapsed_seconds):
        QMetaObject.invokeMethod(self, "_slot_song_progress", Qt.QueuedConnection,
                                 Q_ARG(float, float(elapsed_seconds)))

    @Slot(float)
    def _slot_song_progress(self, elapsed_seconds):
        if not self._countdown_active:
            return
        self._song_elapsed = max(0.0, elapsed_seconds)
        self._render_countdown()

    def _tick_countdown(self):
        if not self._countdown_active:
            return
        remaining = self._song_total - self._song_elapsed
        if remaining <= 0:
            return
        self._render_countdown()

    def _render_countdown(self):
        remaining = max(0, int(round(self._song_total - self._song_elapsed)))
        mm, ss = divmod(remaining, 60)
        self._status_key = "playing_countdown"
        self._status_kwargs = {"m": mm, "s": f"{ss:02d}"}
        self._render_status()

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

    # ---- hidden admin title-hold -----------------------------------------
    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self.title_lbl and self._hold_detector is not None:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._hold_detector.press()
            elif event.type() == QEvent.MouseButtonRelease:
                self._hold_detector.release()
        return super().eventFilter(obj, event)


def build_app(argv):
    app = QApplication(argv)
    app.setStyleSheet(STYLE)
    return app
