from __future__ import annotations

from pathlib import Path
from typing import Any

from mailflow.ui.single_instance import acquire_instance_lock


def test_second_instance_is_refused_until_first_releases(qapp: Any, tmp_path: Path) -> None:
    first = acquire_instance_lock(tmp_path / "data")
    assert first is not None

    assert acquire_instance_lock(tmp_path / "data") is None

    first.unlock()
    second = acquire_instance_lock(tmp_path / "data")
    assert second is not None
    second.unlock()
