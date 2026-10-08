"""Explicit TEST_HAUSIE physical-button contract.

The input_select entities configure a gesture; they are not evidence that a
gesture happened. Only an event entity transition creates a press. A press is
attributed to a physical control, never to a named Home Assistant user.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


CUBE_EVENT = "event.0x54ef44100120ec41_action"
ALI_EVENT = "event.0xa4c1381c42aaf764_action"
IKEA_EVENTS = {f"event.bilresa_dual_button_button_{number}" for number in (1, 2)}
WHEEL_EVENTS = {f"event.bilresa_scroll_wheel_button_{number}" for number in range(1, 10)}
BUTTON_EVENT_IDS = {CUBE_EVENT, ALI_EVENT} | IKEA_EVENTS | WHEEL_EVENTS

CUBE_HELPERS = {
    "flip90": "flip_90_action", "flip180": "flip_180_action",
    "rotate": "rotate_action", "rotate_left": "rotate_action", "rotate_right": "rotate_action",
    "push": "push_action", "throw": "push_action", "slide": "push_action",
    "tap": "tap_action", "shake": "shake_action",
}
ALI_HELPERS = {"single": "single_action", "double": "double_action", "hold": "hold_action"}
IKEA_HELPERS = {"multi_press_1": "single_action", "multi_press_2": "double_action",
                "long_press": "hold_action"}
WHEEL_GESTURES = {*(f"multi_press_{number}" for number in range(1, 9)), "long_press", "long_release"}

BUTTON_HELPER_IDS = {
    "input_boolean.test_hausie_cube_automation_enabled",
    "input_boolean.test_hausie_cube_scene_mode",
    "input_boolean.test_hausie_ali_button_automation_enabled",
    "input_boolean.test_hausie_ikea_button_automation_enabled",
    "input_boolean.test_hausie_button_wheel_automation_enabled",
    "input_select.test_hausie_cube_type",
    "input_select.test_hausie_ali_button_type",
    "input_select.test_hausie_ikea_button_1_type",
    "input_select.test_hausie_button_wheel_type",
}
BUTTON_HELPER_IDS.update(f"input_select.test_hausie_cube_{suffix}" for suffix in CUBE_HELPERS.values())
BUTTON_HELPER_IDS.add("input_select.test_hausie_cube_rotation_action")
BUTTON_HELPER_IDS.update(f"input_select.test_hausie_cube_face_{face}_scene" for face in range(1, 7))
BUTTON_HELPER_IDS.update(f"input_select.test_hausie_ali_button_{suffix}" for suffix in ALI_HELPERS.values())
BUTTON_HELPER_IDS.update(
    f"input_select.test_hausie_ikea_button_{number}_{suffix}"
    for number in (1, 2) for suffix in IKEA_HELPERS.values()
)
BUTTON_HELPER_IDS.update(
    f"input_select.test_hausie_button_wheel_group_{group}_{suffix}_action"
    for group in (1, 2, 3) for suffix in ("rotation", "click")
)

# Mirrors the low-risk destinations in TEST_HAUSIE_button_action_dispatch_script.yaml.
# A script, fan, scene or unmapped selection is still audited as a press but
# cannot confirm a device action without a known safe target.
DESTINATIONS: dict[str, tuple[str, ...]] = {
    "Living Room Lamp": ("light.kajplats_e27_cws_globe_1055lm",),
    "Sofa Lamp 1": ("light.wiz_rgbww_tunable_85d448",),
    "Sofa Lamp 2": ("light.wiz_rgbww_tunable_85cddc",),
    "eWeLight ZB-DL01": ("light.ewelight_zb_dl01",),
    "Bedroom Lamp": ("light.tz3210_r5afgmkl_ts0505b",),
    "Group - Living Room - Secondary Lights": (
        "light.wiz_rgbww_tunable_85cddc", "light.wiz_rgbww_tunable_85d448"),
    "Group - Living Room - Ambient Lights": (
        "light.wiz_rgbww_tunable_85cddc", "light.wiz_rgbww_tunable_85d448"),
    "Group - Living Room - Lights": (
        "light.wiz_rgbww_tunable_85cddc", "light.wiz_rgbww_tunable_85d448"),
    "Group - Kitchen - Lights": ("light.ewelight_zb_dl01",),
    "Group - Bedroom - Secondary Lights": ("light.tz3210_r5afgmkl_ts0505b",),
    "Group - Bedroom - Lights": ("light.tz3210_r5afgmkl_ts0505b",),
    "Group - General - Lights": (
        "light.tz3210_r5afgmkl_ts0505b", "light.wiz_rgbww_tunable_85cddc",
        "light.wiz_rgbww_tunable_85d448"),
    "Blinds": ("cover.core_living_room_blinds",),
    "Living Room - Blinds 1": ("cover.0xc4d8c8fffe343084",),
    "Living Room - Blinds 2": ("cover.0xc4d8c8fffe329156",),
    "Living Room - Blinds 3": ("cover.0xc4d8c8fffe0ce5b3",),
    "Living Room - Blinds 4": ("cover.0xc4d8c8fffea0b9b7",),
    "Bedroom - Blinds 1": ("cover.0xc4d8c8fffe32bfbb",),
    "Bedroom - Blinds 2": ("cover.0xc4d8c8fffe34a6f5",),
    "Group - Living Room - Blinds": ("cover.core_living_room_blinds",),
    "Group - Bedroom - Blinds": ("cover.core_bedroom_blinds",),
    "All Blinds": ("cover.all_blinds",),
}


def _value(states: dict[str, Any], entity_id: str) -> str:
    item = states.get(entity_id)
    return str(item.get("state", "") if isinstance(item, dict) else item or "").strip()


def resolve_press(entity_id: str, event_type: str, attributes: dict[str, Any],
                  states: dict[str, Any], at: datetime, context: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a physical event against the *then-current* helper states."""
    if entity_id not in BUTTON_EVENT_IDS:
        return None
    gesture = event_type.strip().lower()
    helper = ""
    operation = "trigger"
    if entity_id == CUBE_EVENT:
        if _value(states, "input_boolean.test_hausie_cube_automation_enabled") != "on":
            return None
        control_type = _value(states, "input_select.test_hausie_cube_type")
        if control_type == "Dial" and gesture in {"rotate_left", "rotate_right"}:
            helper = "input_select.test_hausie_cube_rotation_action"
        elif control_type == "Cube":
            if _value(states, "input_boolean.test_hausie_cube_scene_mode") == "on":
                if gesture not in {"side_up", "flip_to_side"}:
                    return None
                try:
                    face = int(attributes.get("side_up") or attributes.get("side") or attributes.get("face") or 0)
                except (TypeError, ValueError):
                    return None
                if not 1 <= face <= 6:
                    return None
                helper = f"input_select.test_hausie_cube_face_{face}_scene"
            elif gesture in CUBE_HELPERS:
                helper = f"input_select.test_hausie_cube_{CUBE_HELPERS[gesture]}"
        if gesture == "rotate_right":
            operation = "increase"
        elif gesture == "rotate_left":
            operation = "decrease"
    elif entity_id == ALI_EVENT:
        if (_value(states, "input_boolean.test_hausie_ali_button_automation_enabled") != "on" or
                _value(states, "input_select.test_hausie_ali_button_type") != "Button" or
                gesture not in ALI_HELPERS):
            return None
        helper = f"input_select.test_hausie_ali_button_{ALI_HELPERS[gesture]}"
    elif entity_id in IKEA_EVENTS:
        if (_value(states, "input_boolean.test_hausie_ikea_button_automation_enabled") != "on" or
                _value(states, "input_select.test_hausie_ikea_button_1_type") != "Button" or
                gesture not in IKEA_HELPERS):
            return None
        number = entity_id.rsplit("_", 1)[-1]
        helper = f"input_select.test_hausie_ikea_button_{number}_{IKEA_HELPERS[gesture]}"
    else:
        if (_value(states, "input_boolean.test_hausie_button_wheel_automation_enabled") != "on" or
                _value(states, "input_select.test_hausie_button_wheel_type") != "Dial" or
                gesture not in WHEEL_GESTURES):
            return None
        position = int(entity_id.rsplit("_", 1)[-1])
        group = (position - 1) // 3 + 1
        kind = "click" if position % 3 == 0 else "rotation"
        helper = f"input_select.test_hausie_button_wheel_group_{group}_{kind}_action"
        operation = "increase" if position % 3 == 1 else "decrease" if position % 3 == 2 else "trigger"
    if not helper:
        return None
    selection = _value(states, helper)
    return {"event_entity_id": entity_id, "pressed_at": at.isoformat(), "gesture": gesture,
            "helper_entity_id": helper, "selection": selection, "operation": operation,
            "target_entities": DESTINATIONS.get(selection, ()), "context": context,
            "matched_entities": set()}


def _effect_direction(old_state: dict[str, Any], new_state: dict[str, Any]) -> str | None:
    entity_id = str(new_state.get("entity_id") or "")
    before = old_state.get("attributes") or {}
    after = new_state.get("attributes") or {}
    key = "brightness" if entity_id.startswith("light.") else "current_position" if entity_id.startswith("cover.") else ""
    if key:
        try:
            difference = float(after[key]) - float(before[key])
            if difference:
                return "increase" if difference > 0 else "decrease"
        except (KeyError, TypeError, ValueError):
            pass
    old = str(old_state.get("state") or "").lower()
    new = str(new_state.get("state") or "").lower()
    if entity_id.startswith("light."):
        if old == "off" and new == "on":
            return "increase"
        if old == "on" and new == "off":
            return "decrease"
    if entity_id.startswith("cover."):
        if new in {"open", "opening"} and old in {"closed", "closing"}:
            return "increase"
        if new in {"closed", "closing"} and old in {"open", "opening"}:
            return "decrease"
    return None


def matching_press(presses: list[dict[str, Any]], at: datetime,
                   action: dict[str, Any], old_state: dict[str, Any],
                   new_state: dict[str, Any]) -> dict[str, Any] | None:
    """Only a known target changing soon after its own button may be credited."""
    target = str(action.get("entity_id") or "")
    for press in reversed(presses):
        delay = at - datetime.fromisoformat(press["pressed_at"])
        if (timedelta(0) <= delay <= timedelta(seconds=8) and
                target in press["target_entities"] and target not in press["matched_entities"] and
                (press["operation"] == "trigger" or
                 press["operation"] == _effect_direction(old_state, new_state))):
            press["matched_entities"].add(target)
            return press
    return None


def attribute_effect(old_state: dict[str, Any] | None,
                     new_state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a confirmed button-caused brightness/position change to a safe action.

    This does not by itself attribute the change to a button. The caller must
    still match a recent physical press and an explicit destination.
    """
    if not isinstance(old_state, dict) or not isinstance(new_state, dict):
        return None
    entity_id = str(new_state.get("entity_id") or "")
    if str(old_state.get("state") or "") != str(new_state.get("state") or ""):
        return None
    before = old_state.get("attributes") or {}
    after = new_state.get("attributes") or {}
    if entity_id.startswith("light.") and str(new_state.get("state")) == "on":
        try:
            previous = int(before["brightness"])
            current = int(after["brightness"])
        except (KeyError, TypeError, ValueError):
            return None
        if previous != current and 0 < current <= 255:
            return {"domain": "light", "service": "turn_on", "entity_id": entity_id,
                    "service_data": {"brightness_pct": max(1, round(current * 100 / 255))}}
    if entity_id.startswith("cover."):
        try:
            previous = int(before["current_position"])
            current = int(after["current_position"])
        except (KeyError, TypeError, ValueError):
            return None
        if previous != current and 0 <= current <= 100:
            return {"domain": "cover", "service": "set_cover_position", "entity_id": entity_id,
                    "service_data": {"position": current}}
    return None
