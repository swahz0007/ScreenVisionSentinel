"""Safety controller for read-only defaults."""

from __future__ import annotations

from dataclasses import dataclass


class ActionBlockedError(RuntimeError):
    """Raised when an action is blocked by the safety controller."""


@dataclass(frozen=True)
class SafetyState:
    """Current safety switch state."""

    automatic_actions_enabled: bool = False
    emergency_stopped: bool = False


class SafetyController:
    """Central safety gate for future action execution."""

    def __init__(self, state: SafetyState | None = None) -> None:
        self._state = state or SafetyState()

    @property
    def state(self) -> SafetyState:
        return self._state

    def emergency_stop(self) -> None:
        """Enter emergency stopped state."""
        self._state = SafetyState(
            automatic_actions_enabled=False,
            emergency_stopped=True,
        )

    def ensure_action_allowed(self, action_name: str) -> None:
        """Reject automatic actions unless later stages explicitly enable them."""
        if self._state.emergency_stopped:
            raise ActionBlockedError(f"紧急停止已触发，禁止执行动作: {action_name}")
        if not self._state.automatic_actions_enabled:
            raise ActionBlockedError(f"自动动作默认关闭，禁止执行动作: {action_name}")
