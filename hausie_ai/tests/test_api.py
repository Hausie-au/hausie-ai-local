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
        feedback = client.post(f"/api/v1/decisions/{body['decision_id']}/feedback", json={"reward": -1})
        assert feedback.status_code == 200
        assert client.get("/api/v1/decisions").json()[0]["rated"] is True
        assert client.post(f"/api/v1/decisions/{body['decision_id']}/feedback", json={"reward": 1}).status_code == 404


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


def test_ingress_panel_uses_supervisor_base_path(tmp_path: Path):
    main.service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    with TestClient(main.app) as client:
        response = client.get("/ui", headers={"X-Ingress-Path": "/api/hassio_ingress/example-token"})
        assert response.status_code == 200
        assert '<base href="/api/hassio_ingress/example-token/">' in response.text
        assert 'href="ui/inventory"' in response.text
        assert 'src="ui/assets/app.js?v=0.6.0"' in response.text
        assert client.get("/ui/").status_code == 200
        assert client.get("/ui//").status_code == 200
        assert client.get("http://testserver//ui").status_code == 200
        assert client.get("http://testserver//ui/").status_code == 200
        assert client.get("http://testserver//api/v1/status").status_code == 200
        assert client.get(
            "/api/hassio_ingress/example-token/ui",
            headers={"X-Ingress-Path": "/api/hassio_ingress/example-token"},
        ).status_code == 200
        assert client.get(
            "/api/hassio_ingress/example-token/api/v1/status",
            headers={"X-Ingress-Path": "/api/hassio_ingress/example-token"},
        ).status_code == 200
        assert client.get("/").status_code == 200


def test_separate_ui_pages_filters_and_assets_work_through_ingress(tmp_path: Path):
    main.service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    header = {"X-Ingress-Path": "/api/hassio_ingress/example-token"}
    with TestClient(main.app) as client:
        for page, marker in [
            ("inventory", 'id="filter-area"'),
            ("activity", 'id="events"'),
            ("decisions", 'id="decisions-table"'),
            ("learning", 'id="learning-methods"'),
        ]:
            response = client.get(f"/api/hassio_ingress/example-token/ui/{page}", headers=header)
            assert response.status_code == 200
            assert '<base href="/api/hassio_ingress/example-token/">' in response.text
            assert f'<body data-page="{page}">' in response.text
            assert marker in response.text
        javascript = client.get("/api/hassio_ingress/example-token/ui/assets/app.js", headers=header)
        assert javascript.status_code == 200
        assert javascript.headers["content-type"].startswith("text/javascript")
        assert "filteredEntities" in javascript.text
        stylesheet = client.get("/ui/assets/style.css")
        assert stylesheet.status_code == 200
        assert stylesheet.headers["content-type"].startswith("text/css")
        assert client.get("/ui/assets/unknown.txt").status_code == 404
        assert client.get("/ui/not-a-page").status_code == 404
        assert client.get("http://testserver//ui/inventory").status_code == 200
        comparison = client.get("/api/hassio_ingress/example-token/api/v1/learning/comparison", headers=header)
        assert comparison.status_code == 200
        assert comparison.json() == {"methods": [], "recent": []}
        assert client.get("/api/v1/learning/outcomes").json() == []
        lab = client.get("/api/hassio_ingress/example-token/api/v1/learning/lab", headers=header)
        assert lab.status_code == 200
        assert len(lab.json()["catalog"]) == 15
        assert lab.json()["actions"] == {"methods": [], "recent": []}


def test_supervisor_ingress_entry_does_not_create_double_slash():
    config = (Path(__file__).resolve().parents[1] / "config.yaml").read_text()
    assert "ingress_entry: ui\n" in config
    assert "ingress_entry: /ui" not in config

