import sys
import threading
import time

import pytest

from htmlreview.picker import DialogUnavailable, NO_DIALOG, Picker, PickerBusy

# Stand-ins for the dialog subprocess. The real one is a GUI window.
HANGING = [sys.executable, "-c", "import time; time.sleep(60)"]


def chooses(path):
    return [sys.executable, "-c", f"print({path!r})"]


def test_returns_chosen_path_or_none():
    assert Picker(command=chooses("C:/r/report.html")).pick() == "C:/r/report.html"
    assert Picker(command=chooses("")).pick() is None


def test_no_dialog_available():
    with pytest.raises(DialogUnavailable):
        Picker(command=[sys.executable, "-c", f"import sys; sys.exit({NO_DIALOG})"]).pick()


def test_cancel_closes_a_hanging_dialog():
    picker = Picker(command=HANGING)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("path", picker.pick()))
    t.start()
    time.sleep(0.5)
    with pytest.raises(PickerBusy):
        picker.pick()  # one dialog at a time
    start = time.monotonic()
    assert picker.cancel() is True
    t.join(5)
    assert not t.is_alive() and time.monotonic() - start < 5
    assert result["path"] is None
    assert picker.cancel() is False  # nothing open any more


def test_gives_up_after_timeout():
    start = time.monotonic()
    assert Picker(command=HANGING, timeout=0.5).pick() is None
    assert time.monotonic() - start < 5
