"""Action execution contracts for future controlled operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from screenvision_sentinel.safety.controller import SafetyController


@dataclass(frozen=True)
class ActionRequest:
    """Description of a future user-confirmed action."""

    name: str
    reason: str


class ActionExecutor(Protocol):
    """Protocol for action executors."""

    def execute(self, request: ActionRequest) -> None:
        """Execute an action request when safety gates allow it."""


class DisabledActionExecutor:
    """Executor that refuses all actions unless safety policy changes later."""

    def __init__(self, safety_controller: SafetyController) -> None:
        self._safety_controller = safety_controller

    def execute(self, request: ActionRequest) -> None:
        self._safety_controller.ensure_action_allowed(request.name)
