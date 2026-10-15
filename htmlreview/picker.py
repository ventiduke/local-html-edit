"""Native "open file" dialog.

The server uses :class:`Picker`, which runs this module as a short-lived
subprocess. A separate process is needed because macOS only allows GUI
toolkits on a process's main thread, and the web server owns that thread. It
also means a dialog can always be closed from the browser: cancelling kills
the subprocess, which takes its window with it.

Run directly (``python -m htmlreview.picker [initial_dir]``) it shows the
dialog and prints the chosen path, or nothing if cancelled. Exit code 3 means
no dialog is available on this system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

NO_DIALOG = 3
TIMEOUT = 600  # give up on a dialog nobody answers (it may be hidden)


class DialogUnavailable(RuntimeError):
    pass


class PickerBusy(RuntimeError):
    pass


class Picker:
    """Runs one dialog at a time; :meth:`cancel` closes it from another thread."""

    def __init__(self, timeout: float = TIMEOUT, command: list[str] | None = None):
        self.timeout = timeout
        self.command = command or [sys.executable, "-m", "htmlreview.picker"]
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._lock = threading.Lock()

    def pick(self, initial_dir: str | None = None) -> str | None:
        """Show the dialog; return the chosen path or None if cancelled."""
        cmd = list(self.command)
        if initial_dir:
            cmd.append(initial_dir)
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            # Its own process group, so cancelling also ends osascript (macOS).
            kwargs["start_new_session"] = True
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        with self._lock:
            if self._proc is not None:
                raise PickerBusy("A file dialog is already open.")
            self._cancelled = False
            proc = self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kwargs
            )
        try:
            try:
                out, err = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _kill(proc)
                proc.communicate()
                return None
        finally:
            with self._lock:
                self._proc = None
        if self._cancelled:
            return None
        if proc.returncode == NO_DIALOG:
            raise DialogUnavailable(err.decode("utf-8", "replace").strip())
        if proc.returncode != 0:
            raise RuntimeError(err.decode("utf-8", "replace").strip() or "file dialog failed")
        return out.decode("utf-8").strip() or None

    def cancel(self) -> bool:
        """Close an open dialog. Returns whether one was open."""
        with self._lock:
            if self._proc is None:
                return False
            self._cancelled = True
            _kill(self._proc)
            return True


def _kill(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass


# ---------------------------------------------------------------------------
# The dialog itself (runs in the subprocess)


def _mac_dialog(initial_dir: str | None) -> str:
    script = 'POSIX path of (choose file with prompt "Open HTML file" of type {"public.html"}'
    if initial_dir:
        escaped = initial_dir.replace("\\", "\\\\").replace('"', '\\"')
        script += f' default location (POSIX file "{escaped}")'
    script += ")"
    # "activate" brings osascript's dialog in front of the browser.
    proc = subprocess.run(["osascript", "-e", "activate", "-e", script], capture_output=True, text=True)
    # Cancelling returns an error (-128); treat any failure as "no file".
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _win_bring_to_front(root) -> None:
    """Windows only lets the foreground app raise windows. The tool runs in
    the background (often started by an agent), so its dialog would open
    behind everything. A synthetic Alt key press is the standard way to be
    allowed to take the foreground."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(int(root.wm_frame(), 16))
    except Exception:
        pass


def _tk_dialog(initial_dir: str | None) -> str:
    if sys.platform == "win32":
        try:  # crisp dialog on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.title("HTML Review")
    # A tiny, invisible but *shown* owner window: a withdrawn owner can't take
    # the foreground, and the dialog would open behind the browser.
    root.overrideredirect(True)
    root.geometry("1x1+0+0")
    root.attributes("-alpha", 0.0)
    root.attributes("-topmost", True)
    root.update()
    if sys.platform == "win32":
        _win_bring_to_front(root)
    root.lift()
    root.focus_force()
    root.update()
    path = filedialog.askopenfilename(
        parent=root,
        title="Open HTML file",
        initialdir=initial_dir or None,
        filetypes=[("HTML files", "*.html *.htm"), ("All files", "*.*")],
    )
    root.destroy()
    return path or ""


def main() -> int:
    initial_dir = sys.argv[1] if len(sys.argv) > 1 else None
    if sys.platform == "darwin" and shutil.which("osascript"):
        print(_mac_dialog(initial_dir))
        return 0
    try:
        print(_tk_dialog(initial_dir))
    except Exception as exc:  # ImportError, TclError (no display), ...
        print(f"No file dialog available: {exc}", file=sys.stderr)
        return NO_DIALOG
    return 0


if __name__ == "__main__":
    sys.exit(main())
