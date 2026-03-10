import sys
import threading
from typing import Callable, List


_PRINT_LISTENERS: List[Callable[[str], None]] = []
_PRINT_LISTENER_LOCK = threading.Lock()


def add_print_listener(listener: Callable[[str], None]):
    with _PRINT_LISTENER_LOCK:
        _PRINT_LISTENERS.append(listener)


def remove_print_listener(listener: Callable[[str], None]):
    with _PRINT_LISTENER_LOCK:
        try:
            _PRINT_LISTENERS.remove(listener)
        except ValueError:
            pass


def _emit_print_message(message: str):
    with _PRINT_LISTENER_LOCK:
        listeners = list(_PRINT_LISTENERS)

    for listener in listeners:
        try:
            listener(message)
        except RuntimeError as exc:
            remove_print_listener(listener)
            sys.__stderr__.write(f"[!] Removed log listener: {exc}\n")


def safe_print(*args, **kwargs):
    # Mirror stdout into the GUI log.
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    message = sep.join(str(arg) for arg in args) + end

    try:
        print(*args, **kwargs)
    except (OSError, UnicodeError, ValueError):
        pass

    _emit_print_message(message)


