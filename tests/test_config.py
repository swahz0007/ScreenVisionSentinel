from pathlib import Path

import pytest
from PySide6.QtCore import QRect

from screenvision_sentinel.app.config import (
    AppConfig,
    load_config,
    save_config,
    save_monitor_region,
)
from screenvision_sentinel.capture.base import ScreenRegion
from screenvision_sentinel.ui.main_window import CaptureScreenGeometry
from screenvision_sentinel.vision import CapturePolicy, CapturePolicyError


def test_default_config_keeps_automatic_actions_disabled() -> None:
    config = load_config()

    assert config.screenshot_interval_seconds == 5.0
    assert config.confirmation_count == 3
    assert config.ocr_confidence_threshold == 0.85
    assert config.alert_cooldown_seconds == 30
    assert config.automatic_actions_enabled is False
    assert config.save_trigger_screenshots is False
    assert config.max_capture_width == 7680
    assert config.max_capture_height == 4320
    assert config.max_capture_pixels == 16_777_216
    assert config.monitor_region is None


def test_save_monitor_region_round_trips_local_config(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    region = ScreenRegion(left=10, top=20, width=300, height=120)

    save_monitor_region(config_path, region)
    loaded = load_config(config_path)

    assert loaded.monitor_region == region
    assert loaded.automatic_actions_enabled is False


def test_save_monitor_region_forces_automatic_actions_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    unsafe_config = AppConfig(automatic_actions_enabled=True)

    save_monitor_region(
        config_path,
        ScreenRegion(left=0, top=0, width=100, height=80),
        unsafe_config,
    )
    loaded = load_config(config_path)

    assert loaded.automatic_actions_enabled is False


def test_save_monitor_region_rejects_invalid_size(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"

    with pytest.raises(ValueError):
        save_monitor_region(config_path, ScreenRegion(left=0, top=0, width=0, height=80))


def test_load_config_rejects_string_that_looks_like_false(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        '[safety]\nautomatic_actions_enabled = "false"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="automatic_actions_enabled"):
        load_config(config_path)


def test_load_config_rejects_invalid_confirmation_count(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text("[monitoring]\nconfirmation_count = 0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="confirmation_count"):
        load_config(config_path)


def test_save_monitor_region_enforces_capture_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"

    with pytest.raises(ValueError, match="capture 尺寸限制"):
        save_monitor_region(
            config_path,
            ScreenRegion(left=0, top=0, width=8000, height=10),
        )


def test_save_config_escapes_toml_strings_and_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config = AppConfig(
        ocr_engine='mock"quoted',
        debug_image_dir=Path('data/debug "quoted"'),
    )

    save_config(config_path, config)

    loaded = load_config(config_path)
    assert loaded.ocr_engine == config.ocr_engine
    assert loaded.debug_image_dir == config.debug_image_dir


@pytest.mark.parametrize(
    "values",
    [
        [1, 2, 3],
        [0, 0, 0, 10],
        [0, 0, 10, -1],
        [0, 0, 7681, 10],
        [0, 0, 10, 4321],
        [0, 0, 5000, 5000],
        [0, 0, 1.5, 10],
        [True, 0, 10, 10],
        [-100001, 0, 10, 10],
    ],
)
def test_capture_policy_rejects_invalid_regions(values: list[object]) -> None:
    policy = CapturePolicy()

    with pytest.raises(CapturePolicyError):
        policy.parse_rect_values(values)


def test_capture_policy_accepts_cli_integer_text() -> None:
    policy = CapturePolicy()

    region = policy.parse_csv_rect("10,-20,300,120")

    assert region == ScreenRegion(left=10, top=-20, width=300, height=120)


def test_capture_screen_geometry_maps_qt_logical_region_to_physical_pixels() -> None:
    screen = CaptureScreenGeometry(
        qt_geometry=QRect(0, 0, 2048, 1152),
        capture_left=0,
        capture_top=0,
        capture_width=2560,
        capture_height=1440,
    )

    mapped_region = screen.map_rect_to_capture_region(QRect(739, 550, 825, 317))

    assert mapped_region == ScreenRegion(left=924, top=688, width=1031, height=396)


def test_capture_screen_geometry_maps_offset_secondary_monitor() -> None:
    screen = CaptureScreenGeometry(
        qt_geometry=QRect(2560, -592, 1152, 2048),
        capture_left=2560,
        capture_top=-592,
        capture_width=1440,
        capture_height=2560,
    )

    mapped_region = screen.map_rect_to_capture_region(QRect(2600, -500, 100, 200))

    assert mapped_region == ScreenRegion(left=2610, top=-477, width=125, height=250)
