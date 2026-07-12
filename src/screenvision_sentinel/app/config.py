"""Configuration loading for the current read-only initialization stage."""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from screenvision_sentinel.capture.base import ScreenRegion

DEFAULT_CONFIG_PATH = Path("config/local.toml")
DEFAULT_SCREENSHOT_DIR = Path("data/screenshots")
DEFAULT_DEBUG_DIR = Path("data/debug")


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration with conservative defaults."""

    screenshot_interval_seconds: float = 5.0
    confirmation_count: int = 3
    ocr_confidence_threshold: float = 0.85
    alert_cooldown_seconds: int = 30
    automatic_actions_enabled: bool = False
    save_trigger_screenshots: bool = False
    ocr_engine: str = "rapidocr"
    max_capture_width: int = 7680
    max_capture_height: int = 4320
    max_capture_pixels: int = 16_777_216
    min_capture_coordinate: int = -100_000
    max_capture_coordinate: int = 100_000
    debug_image_dir: Path = DEFAULT_DEBUG_DIR
    server_host: str = "127.0.0.1"
    server_port: int = 8181
    monitor_region: ScreenRegion | None = None


def load_config(path: Path | None = None) -> AppConfig:
    """Load optional TOML config, falling back to safe defaults."""
    if path is None or not path.exists():
        return AppConfig()

    with path.open("rb") as file:
        raw: dict[str, Any] = tomllib.load(file)

    monitoring = _read_table(raw, "monitoring")
    safety = _read_table(raw, "safety")
    ocr_section = _read_table(raw, "ocr")
    capture = _read_table(raw, "capture")
    debug = _read_table(raw, "debug")
    server = _read_table(raw, "server")
    monitor_region = _parse_monitor_region(raw.get("monitor_region"))

    config = AppConfig(
        screenshot_interval_seconds=_read_float(
            monitoring,
            "screenshot_interval_seconds",
            AppConfig.screenshot_interval_seconds,
        ),
        confirmation_count=_read_int(
            monitoring,
            "confirmation_count",
            AppConfig.confirmation_count,
        ),
        ocr_confidence_threshold=_read_float(
            monitoring,
            "ocr_confidence_threshold",
            AppConfig.ocr_confidence_threshold,
        ),
        alert_cooldown_seconds=_read_int(
            monitoring,
            "alert_cooldown_seconds",
            AppConfig.alert_cooldown_seconds,
        ),
        automatic_actions_enabled=_read_bool(
            safety,
            "automatic_actions_enabled",
            AppConfig.automatic_actions_enabled,
        ),
        save_trigger_screenshots=_read_bool(
            safety,
            "save_trigger_screenshots",
            AppConfig.save_trigger_screenshots,
        ),
        ocr_engine=_read_string(ocr_section, "engine", AppConfig.ocr_engine),
        max_capture_width=_read_int(capture, "max_width", AppConfig.max_capture_width),
        max_capture_height=_read_int(capture, "max_height", AppConfig.max_capture_height),
        max_capture_pixels=_read_int(capture, "max_pixels", AppConfig.max_capture_pixels),
        min_capture_coordinate=_read_int(
            capture,
            "min_coordinate",
            AppConfig.min_capture_coordinate,
        ),
        max_capture_coordinate=_read_int(
            capture,
            "max_coordinate",
            AppConfig.max_capture_coordinate,
        ),
        debug_image_dir=Path(_read_string(debug, "image_dir", str(AppConfig.debug_image_dir))),
        server_host=_read_string(server, "host", AppConfig.server_host),
        server_port=_read_int(server, "port", AppConfig.server_port),
        monitor_region=monitor_region,
    )
    _validate_config(config)
    return config


def save_config(path: Path, config: AppConfig) -> None:
    """Write the current local config as a small TOML file."""
    _validate_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_config(config), encoding="utf-8")


def save_monitor_region(path: Path, region: ScreenRegion, config: AppConfig | None = None) -> None:
    """Persist only the monitor region while preserving safe default switches."""
    if not region.is_valid():
        raise ValueError("监控区域宽度和高度必须大于 0")

    base_config = config or load_config(path)
    save_config(
        path,
        AppConfig(
            screenshot_interval_seconds=base_config.screenshot_interval_seconds,
            confirmation_count=base_config.confirmation_count,
            ocr_confidence_threshold=base_config.ocr_confidence_threshold,
            alert_cooldown_seconds=base_config.alert_cooldown_seconds,
            automatic_actions_enabled=False,
            save_trigger_screenshots=base_config.save_trigger_screenshots,
            ocr_engine=base_config.ocr_engine,
            max_capture_width=base_config.max_capture_width,
            max_capture_height=base_config.max_capture_height,
            max_capture_pixels=base_config.max_capture_pixels,
            min_capture_coordinate=base_config.min_capture_coordinate,
            max_capture_coordinate=base_config.max_capture_coordinate,
            debug_image_dir=base_config.debug_image_dir,
            server_host=base_config.server_host,
            server_port=base_config.server_port,
            monitor_region=region,
        ),
    )


def _parse_monitor_region(raw_region: Any) -> ScreenRegion | None:
    if raw_region is None:
        return None
    if not isinstance(raw_region, dict):
        raise ValueError("monitor_region 必须是 TOML 表")

    required_keys = ("left", "top", "width", "height")
    missing_keys = [key for key in required_keys if key not in raw_region]
    if missing_keys:
        joined_keys = ", ".join(missing_keys)
        raise ValueError(f"monitor_region 缺少字段: {joined_keys}")

    region = ScreenRegion(
        left=_strict_int(raw_region["left"], "monitor_region.left"),
        top=_strict_int(raw_region["top"], "monitor_region.top"),
        width=_strict_int(raw_region["width"], "monitor_region.width"),
        height=_strict_int(raw_region["height"], "monitor_region.height"),
    )
    if not region.is_valid():
        raise ValueError("monitor_region 的 width 和 height 必须大于 0")
    return region


def _render_config(config: AppConfig) -> str:
    lines = [
        "[monitoring]",
        f"screenshot_interval_seconds = {config.screenshot_interval_seconds}",
        f"confirmation_count = {config.confirmation_count}",
        f"ocr_confidence_threshold = {config.ocr_confidence_threshold}",
        f"alert_cooldown_seconds = {config.alert_cooldown_seconds}",
        "",
        "[safety]",
        f"automatic_actions_enabled = {_format_bool(config.automatic_actions_enabled)}",
        f"save_trigger_screenshots = {_format_bool(config.save_trigger_screenshots)}",
        "",
        "[ocr]",
        f"engine = {_format_toml_string(config.ocr_engine)}",
        "",
        "[capture]",
        f"max_width = {config.max_capture_width}",
        f"max_height = {config.max_capture_height}",
        f"max_pixels = {config.max_capture_pixels}",
        f"min_coordinate = {config.min_capture_coordinate}",
        f"max_coordinate = {config.max_capture_coordinate}",
        "",
        "[debug]",
        f"image_dir = {_format_toml_string(config.debug_image_dir.as_posix())}",
        "",
        "[server]",
        f"host = {_format_toml_string(config.server_host)}",
        f"port = {config.server_port}",
    ]
    if config.monitor_region is not None:
        lines.extend(
            [
                "",
                "[monitor_region]",
                f"left = {config.monitor_region.left}",
                f"top = {config.monitor_region.top}",
                f"width = {config.monitor_region.width}",
                f"height = {config.monitor_region.height}",
                'coordinate_space = "mss_physical"',
            ]
        )
    return "\n".join(lines) + "\n"


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _format_toml_string(value: str) -> str:
    """Encode a TOML basic string without allowing quotes or newlines to break the file."""
    return json.dumps(value, ensure_ascii=False)


def _read_table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 TOML 表")
    return value


def _read_bool(section: dict[str, Any], name: str, default: bool) -> bool:
    value = section.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return value


def _read_int(section: dict[str, Any], name: str, default: int) -> int:
    return _strict_int(section.get(name, default), name)


def _read_float(section: dict[str, Any], name: str, default: float) -> float:
    value = section.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字")
    return float(value)


def _read_string(section: dict[str, Any], name: str, default: str) -> str:
    value = section.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串")
    return value


def _strict_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    return value


def _strict_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字")
    return float(value)


def _validate_config(config: AppConfig) -> None:
    """Reject unsafe or internally inconsistent runtime configuration."""
    screenshot_interval = _strict_float(
        config.screenshot_interval_seconds,
        "screenshot_interval_seconds",
    )
    if not math.isfinite(screenshot_interval) or screenshot_interval <= 0:
        raise ValueError("screenshot_interval_seconds 必须是大于 0 的有限数字")
    if _strict_int(config.confirmation_count, "confirmation_count") < 1:
        raise ValueError("confirmation_count 必须至少为 1")
    confidence_threshold = _strict_float(
        config.ocr_confidence_threshold,
        "ocr_confidence_threshold",
    )
    if not math.isfinite(confidence_threshold) or not (0 <= confidence_threshold <= 1):
        raise ValueError("ocr_confidence_threshold 必须在 0 到 1 之间")
    if _strict_int(config.alert_cooldown_seconds, "alert_cooldown_seconds") < 0:
        raise ValueError("alert_cooldown_seconds 不能小于 0")
    if not isinstance(config.automatic_actions_enabled, bool):
        raise ValueError("automatic_actions_enabled 必须是布尔值")
    if not isinstance(config.save_trigger_screenshots, bool):
        raise ValueError("save_trigger_screenshots 必须是布尔值")
    if not isinstance(config.ocr_engine, str) or not config.ocr_engine.strip():
        raise ValueError("ocr.engine 必须是非空字符串")
    if not isinstance(config.debug_image_dir, Path):
        raise ValueError("debug.image_dir 必须是路径")

    max_width = _strict_int(config.max_capture_width, "capture.max_width")
    max_height = _strict_int(config.max_capture_height, "capture.max_height")
    max_pixels = _strict_int(config.max_capture_pixels, "capture.max_pixels")
    min_coordinate = _strict_int(config.min_capture_coordinate, "capture.min_coordinate")
    max_coordinate = _strict_int(config.max_capture_coordinate, "capture.max_coordinate")
    if max_width <= 0 or max_height <= 0 or max_pixels <= 0:
        raise ValueError("capture 尺寸和像素限制必须大于 0")
    if min_coordinate >= max_coordinate:
        raise ValueError("capture.min_coordinate 必须小于 capture.max_coordinate")

    if config.server_host != "127.0.0.1":
        raise ValueError("server.host 必须是 127.0.0.1")
    server_port = _strict_int(config.server_port, "server.port")
    if not 1 <= server_port <= 65535:
        raise ValueError("server.port 必须在 1 到 65535 之间")

    region = config.monitor_region
    if region is None:
        return
    if not isinstance(region, ScreenRegion):
        raise ValueError("monitor_region 必须是屏幕区域")
    region_values = {
        "left": _strict_int(region.left, "monitor_region.left"),
        "top": _strict_int(region.top, "monitor_region.top"),
        "width": _strict_int(region.width, "monitor_region.width"),
        "height": _strict_int(region.height, "monitor_region.height"),
    }
    if not region.is_valid():
        raise ValueError("monitor_region 的 width 和 height 必须大于 0")
    if region_values["width"] > max_width or region_values["height"] > max_height:
        raise ValueError("monitor_region 超出 capture 尺寸限制")
    if region_values["width"] * region_values["height"] > max_pixels:
        raise ValueError("monitor_region 超出 capture 像素限制")
    if (
        region_values["left"] < min_coordinate
        or region_values["top"] < min_coordinate
        or region_values["left"] + region_values["width"] > max_coordinate
        or region_values["top"] + region_values["height"] > max_coordinate
    ):
        raise ValueError("monitor_region 坐标超出 capture 允许范围")
