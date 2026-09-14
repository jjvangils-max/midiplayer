"""USB mass-storage detection and MIDI import.

Two strategies:
  * udev monitor (pyudev): event-driven, works when running as a service with
    permissions to read the udev socket.
  * Polling fallback: scan USB_MOUNT_ROOT for newly appeared mount points.

Both call back with the mounted device path so the GUI can ask the user whether
to import MIDI files.
"""

import shutil
import threading
import time
from pathlib import Path

import config
import midi_library


class UsbSignals:
    def on_usb_inserted(self, mount_path): pass
    def on_import_progress(self, n_total, n_done): pass
    def on_import_done(self, n_imported): pass
    def on_import_error(self, msg): pass


class UsbMonitor(threading.Thread):
    POLL_INTERVAL = 2.0

    def __init__(self, signals=None):
        super().__init__(daemon=True)
        self.signals = signals or UsbSignals()
        self._stop = threading.Event()
        self._known = set()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            import pyudev  # noqa
            self._run_udev()
            return
        except Exception:
            pass
        self._run_poll()

    # ---- udev -------------------------------------------------------------
    def _run_udev(self):
        import pyudev
        ctx = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(ctx)
        monitor.filter_by(subsystem="block", device_type="partition")
        for device in iter(monitor.poll, None):
            if self._stop.is_set():
                break
            if device.action != "add":
                continue
            mount = self._wait_for_mount(device.device_node)
            if mount:
                self.signals.on_usb_inserted(mount)

    def _wait_for_mount(self, device_node, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            mount = self._find_mountpoint(device_node)
            if mount:
                return mount
            time.sleep(0.5)
        return None

    @staticmethod
    def _find_mountpoint(device_node):
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == device_node:
                        return parts[1].replace("\\040", " ")
        except Exception:
            return None
        return None

    # ---- polling fallback -------------------------------------------------
    def _run_poll(self):
        self._known = self._current_mounts()
        while not self._stop.is_set():
            time.sleep(self.POLL_INTERVAL)
            current = self._current_mounts()
            for m in current - self._known:
                self.signals.on_usb_inserted(m)
            self._known = current

    @staticmethod
    def _current_mounts():
        mounts = set()
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mp = parts[1].replace("\\040", " ")
                        if mp.startswith(str(config.USB_MOUNT_ROOT)):
                            mounts.add(mp)
        except Exception:
            pass
        return mounts


def import_midi_from_usb(usb_root, signals=None):
    """Copy all MIDI files from usb_root into config.MIDI_DIR. Returns count."""
    signals = signals or UsbSignals()
    config.MIDI_DIR.mkdir(parents=True, exist_ok=True)
    files = midi_library.find_midi_on_usb(usb_root)
    n = 0
    for i, src in enumerate(files):
        dst = config.MIDI_DIR / Path(src).name
        if dst.exists():
            base = dst.stem
            ext = dst.suffix
            k = 1
            while dst.exists():
                dst = config.MIDI_DIR / f"{base}_{k}{ext}"
                k += 1
        try:
            shutil.copy2(src, dst)
            n += 1
        except Exception:
            pass
        signals.on_import_progress(len(files), i + 1)
    signals.on_import_done(n)
    return n
