from hausie_ai.app.safety import SafetyPolicy


def test_safety_allows_low_risk_light_action():
    result = SafetyPolicy().evaluate({"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"})
    assert result.allowed


def test_safety_blocks_climate_and_unknown_entities():
    policy = SafetyPolicy()
    assert not policy.evaluate({"domain": "climate", "service": "turn_on", "entity_id": "climate.home"}).allowed
    assert not policy.evaluate({"domain": "light", "service": "turn_on", "entity_id": "switch.kitchen"}).allowed

