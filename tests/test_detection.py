from screenvision_sentinel.detection.base import ObservationStabilityTracker, TextChangeDetector
from screenvision_sentinel.ocr.base import OCRResult


def make_result(text: str) -> OCRResult:
    return OCRResult(
        text=text,
        confidence=1.0,
        boxes=(),
        elapsed_ms=0.0,
        engine_name="test",
        success=True,
    )


def test_text_change_detector_reports_changed_text() -> None:
    detector = TextChangeDetector()

    result = detector.compare(make_result("A"), make_result("B"))

    assert result.changed is True
    assert result.previous_text == "A"
    assert result.current_text == "B"


def test_text_change_detector_treats_first_observation_as_baseline() -> None:
    detector = TextChangeDetector()

    result = detector.compare(None, make_result("A"))

    assert result.changed is False


def test_observation_tracker_confirms_change_without_exposing_ocr_text() -> None:
    tracker = ObservationStabilityTracker(confirmation_count=2)
    observation = {
        "name": "HR",
        "field_type": "number",
        "validation_status": "valid",
        "is_valid": True,
        "text": "72",
    }

    first = tracker.observe([observation])
    second = tracker.observe([observation])
    third = tracker.observe([observation])
    changed = tracker.observe([{**observation, "text": "80"}])

    assert first[0]["stable"] is False
    assert second[0]["stable"] is True
    assert second[0]["transition"] == "stabilized"
    assert third[0]["transition"] == "stable"
    assert changed[0]["changed"] is True
    assert changed[0]["transition"] == "changed_pending_confirmation"

    status = tracker.status()

    assert status["confirmation_count"] == 2
    assert status["tick_count"] == 4
    assert "text" not in status["fields"][0]
    assert "fingerprint" not in status["fields"][0]


def test_observation_tracker_reset_starts_a_fresh_session() -> None:
    tracker = ObservationStabilityTracker(confirmation_count=2)
    tracker.observe(
        [
            {
                "name": "OLD",
                "field_type": "text",
                "validation_status": "valid",
                "is_valid": True,
                "text": "sensitive value",
            }
        ]
    )

    tracker.reset()

    assert tracker.status()["tick_count"] == 0
    assert tracker.status()["fields"] == []


def test_observation_tracker_removes_fields_missing_from_the_current_tick() -> None:
    tracker = ObservationStabilityTracker(confirmation_count=2)
    tracker.observe(
        [
            {
                "name": "OLD",
                "field_type": "text",
                "validation_status": "valid",
                "is_valid": True,
                "text": "old value",
            }
        ]
    )

    tracker.observe(
        [
            {
                "name": "CURRENT",
                "field_type": "text",
                "validation_status": "valid",
                "is_valid": True,
                "text": "current value",
            }
        ]
    )

    assert [field["name"] for field in tracker.status()["fields"]] == ["CURRENT"]
