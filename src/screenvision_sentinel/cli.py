"""Command-line interface for AHK and local scripts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence

from screenvision_sentinel.app.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SCREENSHOT_DIR,
    AppConfig,
    load_config,
)
from screenvision_sentinel.capture.mss_capture import MssCaptureService
from screenvision_sentinel.ocr.engine_factory import create_ocr_engine
from screenvision_sentinel.vision import CapturePolicy, DebugImageStorage, VisionPipeline

MAX_CLI_REGIONS = 64


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that keeps CLI failures machine-readable."""

    def error(self, message: str) -> None:
        _print_json_error(f"Invalid arguments: {message}")
        raise SystemExit(2)


def main() -> None:
    """CLI entrypoint."""
    raise SystemExit(run())


def run(
    argv: Sequence[str] | None = None,
    *,
    pipeline_factory: Callable[[AppConfig, str], VisionPipeline] | None = None,
) -> int:
    """Run one CLI OCR request and return a process exit code."""
    parser = JsonArgumentParser(description="ScreenVision Sentinel CLI for local scripts")
    parser.add_argument(
        "--rect",
        type=str,
        default="",
        help="Capture region in format left,top,width,height (e.g., 100,100,200,50)",
    )
    parser.add_argument(
        "--rects",
        type=str,
        default="",
        help="Multiple capture regions separated by semicolon (e.g., 1,2,3,4;5,6,7,8)",
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="",
        help="OCR engine name (e.g., rapidocr, mock)",
    )
    parser.add_argument(
        "--save-debug",
        action="store_true",
        help="Save a generated debug screenshot under the configured debug directory",
    )
    args = parser.parse_args(argv)

    if bool(args.rect) == bool(args.rects):
        _print_json_error("Must provide exactly one of --rect or --rects")
        return 1

    try:
        config = load_config(DEFAULT_CONFIG_PATH)
        policy = CapturePolicy.from_config(config)
    except (OSError, ValueError) as exc:
        _print_json_error(f"Invalid configuration: {exc}")
        return 1

    try:
        raw_rects = args.rects.split(";") if args.rects else [args.rect]
        regions = [policy.parse_csv_rect(raw_rect) for raw_rect in raw_rects if raw_rect.strip()]
        if not regions:
            raise ValueError("no non-empty rect values were provided")
        if len(regions) > MAX_CLI_REGIONS:
            raise ValueError(f"at most {MAX_CLI_REGIONS} rects are allowed")
    except (OSError, ValueError) as exc:
        _print_json_error(f"Invalid rect format: {exc}")
        return 1

    engine_name = args.engine or config.ocr_engine
    factory = pipeline_factory or _build_pipeline
    try:
        pipeline = factory(config, engine_name)
        results = [
            pipeline.capture_and_ocr(region, save_debug=args.save_debug).to_dict()
            for region in regions
        ]
    except Exception as exc:
        _print_json_error(f"OCR request failed: {type(exc).__name__}")
        return 1

    if len(results) == 1 and args.rect:
        print(json.dumps(results[0], ensure_ascii=False))
        return 0 if bool(results[0]["success"]) else 1

    output: dict[str, object] = {
        "success": all(bool(result["success"]) for result in results),
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if bool(output["success"]) else 1


def _build_pipeline(config: AppConfig, engine_name: str) -> VisionPipeline:
    return VisionPipeline(
        capture_service=MssCaptureService(DEFAULT_SCREENSHOT_DIR),
        ocr_engine=create_ocr_engine(engine_name),
        policy=CapturePolicy.from_config(config),
        debug_storage=DebugImageStorage(config.debug_image_dir),
    )


def _print_json_error(message: str) -> None:
    output = {
        "success": False,
        "error": message,
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
