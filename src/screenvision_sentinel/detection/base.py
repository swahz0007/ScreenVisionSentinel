"""Content change detection abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from secrets import token_bytes
from time import time
from typing import Protocol

from screenvision_sentinel.ocr.base import OCRResult


@dataclass(frozen=True)
class ChangeDetectionResult:
    """Result of comparing two OCR observations."""

    changed: bool
    previous_text: str
    current_text: str
    reason: str


class BaseChangeDetector(Protocol):
    """Change detector contract."""

    def compare(self, previous: OCRResult | None, current: OCRResult) -> ChangeDetectionResult:
        """Compare two OCR observations."""


class TextChangeDetector:
    """Minimal text comparison detector for tests and stage 0 wiring."""

    def compare(self, previous: OCRResult | None, current: OCRResult) -> ChangeDetectionResult:
        if previous is None:
            return ChangeDetectionResult(
                changed=False,
                previous_text="",
                current_text=current.text,
                reason="没有上一轮识别结果",
            )

        changed = previous.text != current.text
        return ChangeDetectionResult(
            changed=changed,
            previous_text=previous.text,
            current_text=current.text,
            reason="文本发生变化" if changed else "文本未变化",
        )


@dataclass(frozen=True)
class MonitorFieldStatus:
    """Non-sensitive confirmation state for one monitored field."""

    name: str
    field_type: str
    validation_status: str
    consecutive_count: int
    stable: bool
    changed: bool
    transition: str
    observed_at_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "field_type": self.field_type,
            "validation_status": self.validation_status,
            "consecutive_count": self.consecutive_count,
            "stable": self.stable,
            "changed": self.changed,
            "transition": self.transition,
            "observed_at_ms": self.observed_at_ms,
        }


@dataclass
class _TrackedField:
    fingerprint: bytes | None
    status: MonitorFieldStatus


class ObservationStabilityTracker:
    """Confirm repeated valid observations without retaining OCR text in status state."""

    def __init__(self, confirmation_count: int = 3) -> None:
        if confirmation_count < 1:
            raise ValueError("confirmation_count must be at least 1")
        self._confirmation_count = confirmation_count
        self._tick_count = 0
        self._fields: dict[str, _TrackedField] = {}
        self._fingerprint_key = token_bytes(16)

    @property
    def confirmation_count(self) -> int:
        """Return the number of matching valid observations required for stability."""
        return self._confirmation_count

    def reset(self) -> None:
        """Start a fresh observation session without retaining prior value fingerprints."""
        self._tick_count = 0
        self._fields.clear()
        self._fingerprint_key = token_bytes(16)

    def observe(self, results: list[dict[str, object]]) -> list[dict[str, object]]:
        """Record one batch response and return non-sensitive field states."""
        self._tick_count += 1
        statuses: list[dict[str, object]] = []
        observed_names: set[str] = set()
        observed_at_ms = int(time() * 1000)

        for result in results:
            name = str(result.get("name") or "").strip()
            if not name:
                continue
            observed_names.add(name)
            field_type = str(result.get("field_type") or "text")
            validation_status = str(result.get("validation_status") or "unreadable")
            is_valid = bool(result.get("is_valid"))
            previous = self._fields.get(name)

            if not is_valid:
                status = MonitorFieldStatus(
                    name=name,
                    field_type=field_type,
                    validation_status=validation_status,
                    consecutive_count=0,
                    stable=False,
                    changed=False,
                    transition="invalid",
                    observed_at_ms=observed_at_ms,
                )
                self._fields[name] = _TrackedField(fingerprint=None, status=status)
                statuses.append(status.to_dict())
                continue

            text = str(result.get("text") or "")
            fingerprint = blake2b(
                text.encode("utf-8"),
                digest_size=16,
                key=self._fingerprint_key,
            ).digest()
            same_as_previous = previous is not None and previous.fingerprint == fingerprint
            consecutive_count = previous.status.consecutive_count + 1 if same_as_previous else 1
            stable = consecutive_count >= self._confirmation_count
            changed = previous is not None and previous.fingerprint not in {None, fingerprint}
            if stable and (previous is None or not previous.status.stable or changed):
                transition = "stabilized"
            elif stable:
                transition = "stable"
            elif changed:
                transition = "changed_pending_confirmation"
            else:
                transition = "observing"
            status = MonitorFieldStatus(
                name=name,
                field_type=field_type,
                validation_status=validation_status,
                consecutive_count=consecutive_count,
                stable=stable,
                changed=changed,
                transition=transition,
                observed_at_ms=observed_at_ms,
            )
            self._fields[name] = _TrackedField(fingerprint=fingerprint, status=status)
            statuses.append(status.to_dict())

        self._fields = {
            name: tracked for name, tracked in self._fields.items() if name in observed_names
        }
        return statuses

    def status(self) -> dict[str, object]:
        """Return monitor metadata without OCR text or value fingerprints."""
        return {
            "success": True,
            "mode": "manual_tick",
            "confirmation_count": self._confirmation_count,
            "tick_count": self._tick_count,
            "fields": [tracked.status.to_dict() for _name, tracked in sorted(self._fields.items())],
        }
