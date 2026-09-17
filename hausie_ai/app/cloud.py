from __future__ import annotations

from typing import Any

import requests


class CloudClient:
    """Optional control-plane client. It never sends raw Home Assistant states."""

    def __init__(self, base_url: str, token: str, device_id: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.device_id = device_id

    def _headers(self) -> dict[str, str]:
        return {"X-Hausie-AI-Token": self.token} if self.token else {}

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.base_url or not self.device_id:
            return None
        response = requests.post(
            f"{self.base_url}/api/v1/installations/{self.device_id}/heartbeat",
            json=payload,
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def policy(self) -> dict[str, Any] | None:
        if not self.base_url or not self.device_id:
            return None
        response = requests.get(
            f"{self.base_url}/api/v1/installations/{self.device_id}/policy",
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

