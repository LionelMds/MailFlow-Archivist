from __future__ import annotations

import threading
from typing import Any

import pytest
from PySide6.QtCore import QTimer

from mailflow.ui.background_call import run_with_event_loop


def test_network_call_keeps_main_event_loop_responsive(qapp: Any) -> None:
    released = threading.Event()
    main_thread = threading.get_ident()
    callback_threads: list[int] = []

    def network_call() -> str:
        callback_threads.append(threading.get_ident())
        if not released.wait(timeout=3):
            raise TimeoutError("The GUI did not process its timer while waiting")
        return "response"

    QTimer.singleShot(20, released.set)
    assert run_with_event_loop(network_call) == "response"
    assert callback_threads != [main_thread]


def test_background_error_returns_to_caller_and_next_call_succeeds(qapp: Any) -> None:
    def fail() -> None:
        raise ValueError("network unavailable")

    with pytest.raises(ValueError, match="network unavailable"):
        run_with_event_loop(fail)
    assert run_with_event_loop(lambda: 42) == 42
