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


def _drm_set_power(on):
    """Set display DPMS via the DRM property API (the KMS path on Bookworm).

    The sysfs file /sys/class/drm/*/dpms is read-only by design, so the
    property API (libdrm) is required. The DRM fd that Qt eglfs already
    holds is reused via /proc/self/fd, so the app's DRM-master rights
    apply and no sudo is needed. First the connector 'DPMS' property is
    set (On=0/Off=3); if a card has no DPMS property, the crtc 'ACTIVE'
    property (1/0) is tried.
    """
    import ctypes
    import glob
    import logging
    import os
    log = logging.getLogger("midiplayer.screen")

    class Res(ctypes.Structure):
        _fields_ = [("count_fbs", ctypes.c_int), ("fbs", ctypes.c_void_p),
                    ("count_crtcs", ctypes.c_int),
                    ("crtcs", ctypes.POINTER(ctypes.c_uint32)),
                    ("count_connectors", ctypes.c_int),
                    ("connectors", ctypes.POINTER(ctypes.c_uint32)),
                    ("count_encoders", ctypes.c_int),
                    ("encoders", ctypes.c_void_p),
                    ("min_width", ctypes.c_uint32), ("max_width", ctypes.c_uint32),
                    ("min_height", ctypes.c_uint32), ("max_height", ctypes.c_uint32),
                    ("width", ctypes.c_int), ("height", ctypes.c_int)]

    class PropList(ctypes.Structure):
        _fields_ = [("count_props", ctypes.c_uint32), ("pad", ctypes.c_uint32),
                    ("props", ctypes.POINTER(ctypes.c_uint64)),
                    ("prop_values", ctypes.POINTER(ctypes.c_uint64))]

    class Prop(ctypes.Structure):
        _fields_ = [("prop_id", ctypes.c_uint32), ("type", ctypes.c_uint32),
                    ("flags", ctypes.c_uint64), ("name", ctypes.c_char * 32),
                    ("count_values", ctypes.c_int),
                    ("values", ctypes.c_void_p),
                    ("count_enums", ctypes.c_int), ("enums", ctypes.c_void_p),
                    ("count_blobs", ctypes.c_int),
                    ("blob_ids", ctypes.c_void_p)]

    lib = None
    for so in ("libdrm.so.2", "libdrm.so.1", "libdrm.so"):
        try:
            lib = ctypes.CDLL(so)
            break
        except OSError:
            continue
    if lib is None:
        return False
    lib.drmModeGetResources.restype = ctypes.POINTER(Res)
    lib.drmModeObjectGetProperties.restype = ctypes.POINTER(PropList)
    lib.drmModeGetProperty.restype = ctypes.POINTER(Prop)
    lib.drmModeObjectSetProperty.argtypes = [
        ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_uint64]

    CONNECTOR, CRTC = 0, 1

    def set_prop(fd, obj_id, obj_type, want, value):
        props = lib.drmModeObjectGetProperties(fd, obj_id, obj_type)
        if not props:
            return False
        ok = False
        try:
            for i in range(props.contents.count_props):
                prop_id = int(props.contents.props[i])
                prop = lib.drmModeGetProperty(fd, prop_id)
                if not prop:
                    continue
                try:
                    if prop.contents.name.decode(errors="ignore") == want:
                        rc = lib.drmModeObjectSetProperty(
                            fd, obj_id, obj_type, prop_id, value)
                        if rc == 0:
                            ok = True
                finally:
                    lib.drmModeFreeProperty(prop)
        finally:
            lib.drmModeFreeObjectProperties(props)
        return ok

    def try_fd(fd):
        res = lib.drmModeGetResources(fd)
        if not res:
            return False
        try:
            connectors = [int(res.contents.connectors[i])
                          for i in range(res.contents.count_connectors)]
            crtcs = [int(res.contents.crtcs[i])
                     for i in range(res.contents.count_crtcs)]
        finally:
            lib.drmModeFreeResources(res)
        ok = False
        dpms = 0 if on else 3          # DRM_MODE_DPMS_ON / DRM_MODE_DPMS_OFF
        for cid in connectors:
            if set_prop(fd, cid, CONNECTOR, "DPMS", dpms):
                ok = True
        if not ok:
            active = 1 if on else 0
            for cid in crtcs:
                if set_prop(fd, cid, CRTC, "ACTIVE", active):
                    ok = True
        return ok

    fds = []
    try:
        for entry in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink("/proc/self/fd/" + entry)
                if target.startswith("/dev/dri/card"):
                    fds.append(int(entry))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    for fd in fds:
        if try_fd(fd):
            log.info("scherm %s via DRM dpms (hergebruikte fd %d)",
                     "aan" if on else "uit", fd)
            return True
    for path in sorted(glob.glob("/dev/dri/card*")):
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            try:
                lib.drmSetMaster(fd)
            except Exception:
                pass
            if try_fd(fd):
                log.info("scherm %s via DRM dpms (%s)",
                         "aan" if on else "uit", path)
                return True
        finally:
            os.close(fd)
    return False


def _screen_power(on):
    """Put the physical display to sleep / wake it (backlight off/on).

    Strategy (first that works wins):
      1. backlight sysfs (/sys/class/backlight/*/bl_power and brightness):
         the standard interface for DSI/eDP panels.
         Sleep = bl_power FB_BLANK_POWERDOWN (4); wake = restore brightness.
      2. DRM object properties via libdrm (connector 'DPMS', else crtc
         'ACTIVE'): the KMS path on Bookworm for HDMI panels. The sysfs file
         /sys/class/drm/*/dpms is read-only by design, so this needs the
         property API; the DRM fd Qt eglfs already holds is reused, so the
         app's DRM-master status is enough (no sudo).
      3. xset dpms force off/on (X11 sessions).
      4. vcgencmd display_power (legacy firmware path, pre-KMS).
      5. fb0/blank (legacy framebuffer).
    Failures are logged and ignored so a dev machine keeps the software dim.
    """
    import glob
    import logging
    import subprocess
    log = logging.getLogger("midiplayer.screen")

    # --- 1. backlight sysfs ------------------------------------------------
    backlights = sorted(glob.glob("/sys/class/backlight/*"))
    for bl in backlights:
        try:
            power_path = f"{bl}/bl_power"
            bright_path = f"{bl}/brightness"
            with open(f"{bl}/max_brightness") as f:
                max_b = int(f.read().strip())
            if on:
                with open(bright_path) as f:
                    cur = int(f.read().strip())
                val = cur if cur > 0 else max_b
                _sysfs_write(power_path, 0)      # FB_BLANK_UNBLANK
                _sysfs_write(bright_path, val)
            else:
                _sysfs_write(power_path, 4)      # FB_BLANK_POWERDOWN
            log.info("scherm %s via backlight sysfs (%s)",
                     "aan" if on else "uit", bl)
            return True
        except Exception:
            continue

    # --- 2. DRM object properties via libdrm (KMS / Bookworm HDMI panels) ----
    if _drm_set_power(on):
        return True

    # --- 3/4/5. subprocess fallbacks ----------------------------------------
    onoff = "1" if on else "0"
    cmds = (
        ["xset", "dpms", "force", "on" if on else "off"],
        ["vcgencmd", "display_power", onoff],
        ["sudo", "-n", "sh", "-c",
         f"echo {onoff} > /sys/class/graphics/fb0/blank"],
    )
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True, timeout=3,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("scherm %s via %s", "aan" if on else "uit", cmd[0])
            return True
        except Exception:
            continue
    log.warning("geen scherm-methode werkte (backlight noch DRM noch tools)")
    return False


def _sysfs_write(path, value):
    """Write to a sysfs file, via sudo -n when the direct write is denied."""
    try:
        with open(path, "w") as f:
            f.write(f"{value}\n")
        return True
    except PermissionError:
        import subprocess
        try:
            subprocess.run(["sudo", "-n", "tee", path],
                           input=f"{value}\n".encode(), check=True, timeout=3,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _shutdown_pi():
    """Power off the Raspberry Pi (from the admin menu)."""
    import subprocess
    for cmd in (["sudo", "-n", "poweroff"], ["sudo", "-n", "shutdown", "-h", "now"]):
        try:
            subprocess.run(cmd, check=True, timeout=5)
            return True
        except Exception:
            continue
    return False


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
        self._attract_overlay = None
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
        # Insufficient-credit warning: big centered overlay, 5 s.
        self._warmup_overlay = None
        self._credit_overlay = None
        self._credit_timer = QTimer(self)
        self._credit_timer.setSingleShot(True)
        self._credit_timer.timeout.connect(self._hide_insufficient_credit)
        # Attract cycle: while idle/asleep, light the screen every
        # ATTRACT_INTERVAL for ATTRACT_DURATION with a coin invitation.
        self._attract_timer = QTimer(self)
        self._attract_timer.timeout.connect(self._show_attract)
        self._attract_timer.start(config.ATTRACT_INTERVAL * 1000)
        self._attract_hide_timer = QTimer(self)
        self._attract_hide_timer.setSingleShot(True)
        self._attract_hide_timer.timeout.connect(self._hide_attract)

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
        if self._status_key == "warmup":
            self._show_warmup_overlay(self._status_kwargs.get("s"))
        else:
            self._hide_warmup_overlay()

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
        # Any playback state change ends the warmup overlay.
        self._hide_warmup_overlay()

    # ---- screen sleep / wake ------------------------------------------------
    def is_asleep(self):
        return not self._awake

    def on_coin_inserted(self):
        """A coin was accepted: wake the kiosk and show the welcome overlay.

        Called on the GUI thread (wiring marshals this). Only when the screen
        was asleep (dimmed) or showing the attract glow; if the kiosk is
        already awake and interactive, the coin just adds credit silently.
        """
        QMetaObject.invokeMethod(self, "_slot_coin_inserted", Qt.QueuedConnection)

    @Slot()
    def _slot_coin_inserted(self):
        attract_visible = getattr(self, "_attract_overlay", None) is not None
        if self._awake and not attract_visible:
            return
        if attract_visible:
            self._attract_hide_timer.stop()
            self._hide_attract_overlay_only()
        self.show_welcome()

    def set_screen_awake(self, awake):
        QMetaObject.invokeMethod(self, "_slot_screen_awake", Qt.QueuedConnection,
                                 Q_ARG(bool, bool(awake)))

    @Slot(bool)
    def _slot_screen_awake(self, awake):
        if awake == self._awake:
            return
        self._awake = awake
        if awake:
            self._attract_hide_timer.stop()
            self._hide_attract_overlay_only()
            self._remove_dim()
            _screen_power(True)
        else:
            self._apply_dim()
            _screen_power(False)

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
        # After the welcome: sleep again when the motor is off and no credit
        # is left (e.g. at startup, or a wake without a usable coin).
        hw = getattr(self, "hw", None)
        if hw is not None and not hw.is_relay_on() \
                and getattr(hw, "balance_cents", 0) <= 0:
            self._slot_screen_awake(False)

    # ---- warmup overlay ------------------------------------------------
    def _show_warmup_overlay(self, seconds):
        if self._warmup_overlay is not None:
            self._warmup_secs_lbl.setText(i18n.t("warmup_secs", s=int(seconds or 0)))
            return
        from PySide6.QtWidgets import QWidget as _W
        overlay = _W(self)
        overlay.setStyleSheet("background: rgba(0, 0, 0, 190);")
        overlay.setGeometry(self.rect())
        lay = QVBoxLayout(overlay)
        lay.addStretch(1)
        msg = QLabel(i18n.t("waiting_organ"))
        msg.setStyleSheet("font-size: 40px; font-weight: bold; color: #ffd166;"
                          "background: transparent;")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        lay.addWidget(msg)
        self._warmup_secs_lbl = QLabel("")
        self._warmup_secs_lbl.setStyleSheet("font-size: 32px; color: #e8eef2;"
                                             "background: transparent;")
        self._warmup_secs_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._warmup_secs_lbl)
        lay.addStretch(1)
        overlay.show()
        overlay.raise_()
        self._warmup_overlay = overlay
        self._warmup_secs_lbl.setText(i18n.t("warmup_secs", s=int(seconds or 0)))

    def _hide_warmup_overlay(self):
        if self._warmup_overlay is not None:
            self._warmup_overlay.deleteLater()
            self._warmup_overlay = None

    # ---- insufficient credit ------------------------------------------------
    def show_insufficient_credit(self, missing_cents):
        QMetaObject.invokeMethod(self, "_slot_insufficient_credit",
                                 Qt.QueuedConnection, Q_ARG(int, int(missing_cents)))

    @Slot(int)
    def _slot_insufficient_credit(self, missing_cents):
        if self._credit_overlay is not None:
            self._credit_timer.stop()
            self._credit_overlay.deleteLater()
        from PySide6.QtWidgets import QWidget as _W
        overlay = _W(self)
        overlay.setStyleSheet("background: rgba(0, 0, 0, 190);")
        overlay.setGeometry(self.rect())
        lay = QVBoxLayout(overlay)
        lay.addStretch(1)
        msg = QLabel(i18n.t("insufficient_title"))
        msg.setStyleSheet("font-size: 40px; font-weight: bold; color: #d93939;"
                          "background: transparent;")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        lay.addWidget(msg)
        amount = f"{missing_cents / 100:.2f}"
        if i18n.get_language() == "fr":
            amount = amount.replace(".", ",")
        sub = QLabel(i18n.t("insufficient_amount", amount=amount))
        sub.setStyleSheet("font-size: 32px; font-weight: bold; color: #ffd166;"
                          "background: transparent;")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addStretch(1)
        overlay.show()
        overlay.raise_()
        self._credit_overlay = overlay
        self._credit_timer.start(5000)

    @Slot()
    def _hide_insufficient_credit(self):
        if self._credit_overlay is not None:
            self._credit_overlay.deleteLater()
            self._credit_overlay = None

    # ---- attract cycle -------------------------------------------------------
    def _show_attract(self):
        if getattr(self, "_awake", True) or self._welcome_overlay is not None:
            return
        # Wake the screen first (this also clears a stale attract overlay),
        # then show the attract message and arm its hide timer.
        self._slot_screen_awake(True)
        import settings
        price = settings.get_song_price_cents()
        amount = f"{price / 100:.2f}".replace(".", ",")
        self._attract_overlay = self._make_center_overlay(
            i18n.t("attract_msg", amount=amount))
        self._attract_hide_timer.start(config.ATTRACT_DURATION * 1000)

    @Slot()
    def _hide_attract(self):
        self._hide_attract_overlay_only()
        self._slot_screen_awake(False)

    def _hide_attract_overlay_only(self):
        ov = getattr(self, "_attract_overlay", None)
        if ov is not None:
            ov.deleteLater()
            self._attract_overlay = None

    def _make_center_overlay(self, text, sub=None):
        from PySide6.QtWidgets import QWidget as _W
        overlay = _W(self)
        overlay.setStyleSheet("background: #000000;")
        overlay.setGeometry(self.rect())
        lay = QVBoxLayout(overlay)
        lay.addStretch(1)
        msg = QLabel(text)
        msg.setStyleSheet("font-size: 36px; font-weight: bold; color: #ffd166;"
                          "background: transparent;")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        lay.addWidget(msg)
        if sub:
            s = QLabel(sub)
            s.setStyleSheet("font-size: 22px; color: #e8eef2;"
                            "background: transparent;")
            s.setAlignment(Qt.AlignCenter)
            s.setWordWrap(True)
            lay.addWidget(s)
        lay.addStretch(1)
        overlay.show()
        overlay.raise_()
        return overlay

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
