from pathlib import Path

from fastapi.testclient import TestClient

from hausie_ai.app import main
from hausie_ai.app.settings import Settings
from hausie_ai.app.service import HausieAIService


def test_observe_and_decide_api(tmp_path: Path):
    settings = Settings.from_environment().with_data_dir(tmp_path)
    main.service = HausieAIService(settings)
    with TestClient(main.app) as client:
        payload = {
            "context": {"weekday": 1, "hour_bucket": 80, "occupancy": "occupied"},
            "action": {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"},
            "source": "user",
        }
        for _ in range(3):
            assert client.post("/api/v1/observe", json=payload).status_code == 200
        response = client.post("/api/v1/decide", json={"context": payload["context"]})
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "SUGGEST_ACTION"
        assert body["executed"] is False


def test_inventory_and_environment_endpoints(tmp_path: Path):
    settings = Settings.from_environment().with_data_dir(tmp_path)
    main.service = HausieAIService(settings)
    main.service.collect_states([
        {
            "entity_id": "sensor.office_temperature", "state": "20.4",
            "attributes": {"device_class": "temperature", "unit_of_measurement": "°C"},
        },
        {"entity_id": "light.office", "state": "off", "attributes": {}},
    ])
    with TestClient(main.app) as client:
        inventory = client.get("/api/v1/inventory")
        assert inventory.status_code == 200
        assert inventory.json()["summary"]["environmental_inputs"] == 1
        assert client.get("/api/v1/context").json()["context"]["environment"]
        assert client.get("/api/v1/environment/events").status_code == 200

