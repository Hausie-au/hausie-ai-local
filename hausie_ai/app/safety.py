from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str


class SafetyPolicy:
    """Deterministic guardrail that is independent from the learner."""

    allowed_services = {
        "light": {"turn_on", "turn_off", "toggle"},
        "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position"},
        "media_player": {"turn_on", "turn_off", "media_play", "media_pause", "media_stop"},
    }
    blocked_domains = {
        "alarm_control_panel",
        "camera",
        "climate",
        "door",
        "garage_door",
        "lock",
        "number",
        "security",
        "select",
        "switch",
    }

    def evaluate(self, action: dict[str, Any]) -> SafetyResult:
        domain = str(action.get("domain", "")).strip()
        service = str(action.get("service", "")).strip()
        entity_id = str(action.get("entity_id", "")).strip()
        if domain in self.blocked_domains:
            return SafetyResult(False, f"Domain '{domain}' is outside the PoC safety policy.")
        if domain not in self.allowed_services:
            return SafetyResult(False, f"Domain '{domain}' is not allow-listed.")
        if service not in self.allowed_services[domain]:
            return SafetyResult(False, f"Service '{domain}.{service}' is not allow-listed.")
        if not entity_id.startswith(f"{domain}."):
            return SafetyResult(False, "The entity_id must belong to the action domain.")
        if "," in entity_id or " " in entity_id:
            return SafetyResult(False, "Only one entity_id is allowed in the PoC.")
        return SafetyResult(True, "Action is within the deterministic low-risk policy.")

