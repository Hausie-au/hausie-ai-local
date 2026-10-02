from hausie_ai.app.safety import SafetyPolicy
from hausie_ai.app.service import action_from_state_change


def test_safety_allows_low_risk_light_action():
    result = SafetyPolicy().evaluate({"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"})
    assert result.allowed


def test_safety_blocks_climate_and_unknown_entities():
    policy = SafetyPolicy()
    assert not policy.evaluate({"domain": "climate", "service": "turn_on", "entity_id": "climate.home"}).allowed
    assert not policy.evaluate({"domain": "light", "service": "turn_on", "entity_id": "switch.kitchen"}).allowed


def test_only_known_safe_state_changes_become_actions():
    assert action_from_state_change(
        {"entity_id": "light.kitchen", "state": "off"},
        {"entity_id": "light.kitchen", "state": "unavailable"},
    ) is None
    assert action_from_state_change(
        {"entity_id": "cover.kitchen", "state": "closed"},
        {"entity_id": "cover.kitchen", "state": "opening"},
    ) == {"domain": "cover", "service": "open_cover", "entity_id": "cover.kitchen"}

