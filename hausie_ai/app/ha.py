from __future__ import annotations

from typing import Any

import requests


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def get_states(self) -> list[dict[str, Any]]:
        response = self.session.get(f"{self.base_url}/api/states", timeout=10)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def call_service(self, action: dict[str, Any]) -> list[dict[str, Any]]:
        domain = action["domain"]
        service = action["service"]
        data: dict[str, Any] = {"entity_id": action["entity_id"]}
        data.update(action.get("service_data") or {})
        response = self.session.post(f"{self.base_url}/api/services/{domain}/{service}", json=data, timeout=10)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

