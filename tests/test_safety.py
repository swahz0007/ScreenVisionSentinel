import pytest

from screenvision_sentinel.actions.base import ActionRequest, DisabledActionExecutor
from screenvision_sentinel.safety.controller import ActionBlockedError, SafetyController


def test_actions_are_blocked_by_default() -> None:
    executor = DisabledActionExecutor(SafetyController())

    with pytest.raises(ActionBlockedError):
        executor.execute(ActionRequest(name="click", reason="test"))


def test_emergency_stop_keeps_actions_blocked() -> None:
    controller = SafetyController()
    controller.emergency_stop()

    assert controller.state.automatic_actions_enabled is False
    assert controller.state.emergency_stopped is True
    with pytest.raises(ActionBlockedError):
        controller.ensure_action_allowed("click")
