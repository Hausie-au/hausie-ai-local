from pathlib import Path

from hausie_ai.app.learning import Learner
from hausie_ai.app.service import changed_low_risk_actions
from hausie_ai.app.storage import Store


def test_repeated_user_observations_produce_a_suggestion(tmp_path: Path):
    store = Store(tmp_path)
    learner = Learner(store, min_observations=3, min_confidence=0.8)
    context = {"weekday": 1, "hour_bucket": 80, "occupancy": "occupied"}
    action = {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"}

    assert learner.recommend(context)[0] is None
    for _ in range(3):
        learner.observe(context, action, "user")

    candidate, reason = learner.recommend(context)
    assert candidate is not None
    assert candidate.action == action
    assert candidate.confidence == 1.0
    assert "repeated" in reason


def test_changed_states_are_translated_to_low_risk_actions():
    previous = [{"entity_id": "light.living_room", "state": "off"}]
    current = [{"entity_id": "light.living_room", "state": "on"}]
    assert changed_low_risk_actions(previous, current) == [
        {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"}
    ]

