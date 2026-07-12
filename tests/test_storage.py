import sqlite3
from pathlib import Path

import numpy as np

from screenvision_sentinel.storage.sqlite_store import initialize_database
from screenvision_sentinel.vision import DebugImageStorage


def test_initialize_database_creates_expected_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "screenvision.sqlite3"

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "app_events" in table_names
    assert "app_config" in table_names


def test_debug_storage_saves_under_fixed_directory_without_overwriting(tmp_path: Path) -> None:
    storage = DebugImageStorage(tmp_path / "debug")
    image = np.zeros((4, 4, 3), dtype=np.uint8)

    first = storage.save(image)
    second = storage.save(image)

    assert first.parent == tmp_path / "debug"
    assert second.parent == tmp_path / "debug"
    assert first.suffix == ".png"
    assert second.suffix == ".png"
    assert first != second
    assert first.exists()
    assert second.exists()
