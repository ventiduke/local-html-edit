"""Native "open file" dialog.

The server calls :func:`pick_file`, which runs this module as a short-lived
subprocess. A separate process is needed because macOS only allows GUI
toolkits on a process's main thread, and the web server owns that thread.

Run directly (``python -m htmlreview.picker [initial_dir]``) it shows the
dialog and prints the chosen path, or nothing if cancelled. Exit code 3 means
no dialog is available on this system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

NO_DIALOG = 3


class DialogUnavailable(RuntimeError):
    pass


def pick_file(initial_dir: str | None = None) -> str | None:
    """Show the dialog; return the chosen path or None if cancelled."""
    cmd = [sys.executable, "-m", "htmlreview.picker"]
    if initial_dir:
        cmd.append(initial_dir)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, capture_output=True, env=env, **kwargs)
    if proc.returncode == NO_DIALOG:
        raise DialogUnavailable(proc.stderr.decode("utf-8", "replace").strip())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "file dialog failed")
    path = proc.stdout.decode("utf-8").strip()
    return path or None


def _mac_dialog(initial_dir: str | None) -> str:
    script = 'POSIX path of (choose file with prompt "Open HTML file" of type {"public.html"}'
    if initial_dir:
        escaped = initial_dir.replace("\\", "\\\\").replace('"', '\\"')
        script += f' default location (POSIX file "{escaped}")'
    script += ")"
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    # Cancelling returns an error (-128); treat any failure as "no file".
    return proc.stdout.strip() if proc.returncode == 0 else ""


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
    root.withdraw()
    # Without this the dialog often opens behind the browser window.
    root.attributes("-topmost", True)
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
