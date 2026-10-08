from dataclasses import replace
from datetime import datetime, timedelta, timezone

from hausie_ai.app.buttons import (ALI_EVENT, CUBE_EVENT, DESTINATIONS,
                                   matching_press, resolve_press)
from hausie_ai.app.history import HistoryBootstrap
from hausie_ai.app.inventory import InventoryBuilder
from hausie_ai.app.lab_storage import LabStore
from hausie_ai.app.safety import SafetyPolicy
from hausie_ai.app.service import HausieAIService
from hausie_ai.app.settings import Settings
from hausie_ai.app.storage import Store
from hausie_ai.tests.test_history import FakeHA, sample


LIGHT = "light.kajplats_e27_cws_globe_1055lm"
IKEA = "event.bilresa_dual_button_button_1"
ENABLED = "input_boolean.test_hausie_ikea_button_automation_enabled"
CONTROL = "input_select.test_hausie_ikea_button_1_type"
MAPPING = "input_select.test_hausie_ikea_button_1_single_action"


def state(entity_id, value, at, attributes=None):
    return {"entity_id": entity_id, "state": value, "attributes": attributes or {},
            "last_changed": at.isoformat()}


def test_button_resolver_uses_event_and_then_current_helper_only():
    at = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    states = {ENABLED: "on", CONTROL: "Button", MAPPING: "Living Room Lamp"}
    press = resolve_press(IKEA, "multi_press_1", {}, states, at, {"occupancy": "occupied"})
    assert press["helper_entity_id"] == MAPPING
    assert press["target_entities"] == (LIGHT,)
    assert press["context"]["occupancy"] == "occupied"
    states[MAPPING] = "Sofa Lamp 1"
    assert press["target_entities"] == (LIGHT,)  # changing the helper later does not rewrite the press
    assert resolve_press(IKEA, "multi_press_1", {}, {**states, ENABLED: "off"}, at, {}) is None
    assert resolve_press(IKEA, "unknown", {}, states, at, {}) is None
    assert resolve_press(ALI_EVENT, "single", {}, states, at, {}) is None
    assert "Fan" not in DESTINATIONS  # blocked/unknown effects stay in the press audit only
    cube = resolve_press(CUBE_EVENT, "rotate_right", {}, {
        "input_boolean.test_hausie_cube_automation_enabled": "on",
        "input_select.test_hausie_cube_type": "Dial",
        "input_select.test_hausie_cube_rotation_action": "Living Room Lamp",
    }, at, {})
    assert cube["operation"] == "increase"


def test_rotary_press_rejects_opposite_device_direction():
    at = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    press = resolve_press(CUBE_EVENT, "rotate_right", {}, {
        "input_boolean.test_hausie_cube_automation_enabled": "on",
        "input_select.test_hausie_cube_type": "Dial",
        "input_select.test_hausie_cube_rotation_action": "Living Room Lamp",
    }, at, {})
    action = {"domain": "light", "service": "turn_on", "entity_id": LIGHT,
              "service_data": {"brightness_pct": 20}}
    assert matching_press([press], at + timedelta(seconds=1), action,
                          {"entity_id": LIGHT, "state": "on", "attributes": {"brightness": 150}},
                          {"entity_id": LIGHT, "state": "on", "attributes": {"brightness": 50}}) is None


def test_live_button_effect_trains_once_without_naming_a_user(tmp_path):
    settings = replace(Settings.from_environment().with_data_dir(tmp_path),
                       history_import_days=0, learn_from_unknown=False)
    service = HausieAIService(settings)
    at = datetime.now(timezone.utc)
    initial = [state(ENABLED, "on", at), state(CONTROL, "Button", at),
               state(MAPPING, "Living Room Lamp", at), state(IKEA, (at - timedelta(days=1)).isoformat(), at),
               state(LIGHT, "off", at), state("light.unrelated", "off", at)]
    service.collect_states(initial)
    press_at = at + timedelta(seconds=1)
    event_state = state(IKEA, press_at.isoformat(), press_at, {"event_type": "multi_press_1"})
    service.handle_state_changed({"data": {"old_state": initial[3], "new_state": event_state}})
    assert service.store.button_counts() == {"presses": 1, "confirmed_actions": 0}
    assert service.store.stats()["observations"] == 0
    unrelated_at = press_at + timedelta(seconds=1)
    service.handle_state_changed({"data": {"old_state": initial[5],
                                          "new_state": state("light.unrelated", "on", unrelated_at,
                                                             {"friendly_name": "Other"},),
                                          }, "context": {"parent_id": "automation"}})
    assert service.store.stats()["observations"] == 0
    effect_at = press_at + timedelta(seconds=2)
    effect = {"data": {"old_state": initial[4], "new_state": state(LIGHT, "on", effect_at)},
              "context": {"parent_id": "automation"}}
    service.handle_state_changed(effect)
    service.handle_state_changed(effect)
    assert service.store.button_counts() == {"presses": 1, "confirmed_actions": 1}
    assert service.store.stats()["observations"] == 1
    assert service.store.recent()[0]["source"] == "physical_button"
    assert service.store.recent_button_presses()[0]["confirmed_actions"] == 1
    assert service.last_event_source == "automation"  # duplicate is not credited again


def test_live_dial_brightness_change_is_a_confirmed_button_action(tmp_path):
    service = HausieAIService(replace(Settings.from_environment().with_data_dir(tmp_path), history_import_days=0))
    at = datetime.now(timezone.utc)
    old_light = state(LIGHT, "on", at, {"brightness": 100})
    states = [state("input_boolean.test_hausie_cube_automation_enabled", "on", at),
              state("input_select.test_hausie_cube_type", "Dial", at),
              state("input_select.test_hausie_cube_rotation_action", "Living Room Lamp", at),
              state(CUBE_EVENT, (at - timedelta(days=1)).isoformat(), at), old_light]
    service.collect_states(states)
    pressed = at + timedelta(seconds=1)
    service.handle_state_changed({"data": {"old_state": states[3],
                                          "new_state": state(CUBE_EVENT, pressed.isoformat(), pressed,
                                                             {"event_type": "rotate_right"})}})
    after = state(LIGHT, "on", pressed + timedelta(seconds=1), {"brightness": 153})
    service.handle_state_changed({"data": {"old_state": old_light, "new_state": after},
                                  "context": {"parent_id": "automation"}})
    learned = service.store.recent()[0]
    assert learned["source"] == "physical_button"
    assert learned["action"]["service_data"] == {"brightness_pct": 60}
    assert service._action_is_needed(learned["action"]) is False
    assert service.store.button_counts() == {"presses": 1, "confirmed_actions": 1}


def test_history_imports_button_mapping_at_press_time_and_promotes_prior_automation(tmp_path):
    store = Store(tmp_path)
    LabStore(store)
    safety = SafetyPolicy()
    cutoff = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    baseline = cutoff - timedelta(days=2)
    pressed = cutoff - timedelta(hours=1)
    changed = pressed + timedelta(seconds=2)
    rows = [
        sample(baseline, LIGHT, "off"), sample(changed, LIGHT, "on"),
        sample(baseline, ENABLED, "on"), sample(baseline, CONTROL, "Button"),
        sample(baseline, MAPPING, "Living Room Lamp"),
        state(IKEA, pressed.isoformat(), pressed, {"event_type": "multi_press_1"}),
        sample(pressed + timedelta(minutes=1), MAPPING, "Sofa Lamp 1"),
    ]
    ha = FakeHA(rows, [{"entity_id": LIGHT, "when": changed.isoformat(), "state": "on",
                        "context_domain": "automation"}])
    before_profiles = InventoryBuilder(safety).build([state(LIGHT, "off", baseline)])
    first = HistoryBootstrap(ha, store, safety, before_profiles, timezone.utc, 1).run(cutoff)
    assert first["actions"] == 0
    assert store.recent_historical_actions()[0]["source"] == "automation"

    all_states = [state(entity_id, "on", baseline) for entity_id in (ENABLED,)] + [
        state(CONTROL, "Button", baseline), state(MAPPING, "Sofa Lamp 1", baseline),
        state(IKEA, pressed.isoformat(), pressed, {"event_type": "multi_press_1"}),
        state(LIGHT, "on", changed),
    ]
    profiles = InventoryBuilder(safety).build(all_states)
    second = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert second["button_presses"] == 1
    assert second["button_effects"] == 1
    assert second["actions"] == 1
    assert store.recent()[0]["source"] == "historical_button"
    assert store.recent_historical_actions()[0]["source"] == "physical_button"
    assert store.recent_button_presses()[0]["selection"] == "Living Room Lamp"
    assert store.button_counts() == {"presses": 1, "confirmed_actions": 1}
    again = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert again["already_imported"] is True
    assert store.stats()["observations"] == 1


def test_history_imports_attribute_only_dial_effect(tmp_path):
    store = Store(tmp_path)
    LabStore(store)
    safety = SafetyPolicy()
    cutoff = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    baseline = cutoff - timedelta(days=2)
    pressed = cutoff - timedelta(hours=1)
    effect = pressed + timedelta(seconds=2)
    initial = state(LIGHT, "on", baseline, {"brightness": 100})
    later = state(LIGHT, "on", baseline, {"brightness": 153})
    later["last_updated"] = effect.isoformat()
    rows = [initial, later,
            sample(baseline, "input_boolean.test_hausie_cube_automation_enabled", "on"),
            sample(baseline, "input_select.test_hausie_cube_type", "Dial"),
            sample(baseline, "input_select.test_hausie_cube_rotation_action", "Living Room Lamp"),
            state(CUBE_EVENT, pressed.isoformat(), pressed, {"event_type": "rotate_right"})]
    profiles = InventoryBuilder(safety).build([
        initial, rows[2], rows[3], rows[4], rows[5],
    ])
    result = HistoryBootstrap(FakeHA(rows, []), store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert result["actions"] == 1
    assert store.recent()[0]["action"]["service_data"] == {"brightness_pct": 60}
    assert store.recent_historical_actions()[0]["source"] == "physical_button"
