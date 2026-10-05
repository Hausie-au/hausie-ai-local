"""Small server-rendered shell for the local Ingress UI."""

from functools import lru_cache
from html import escape
from pathlib import Path
import re

from fastapi import HTTPException, Request

from .service import ADDON_VERSION


WEB_DIR = Path(__file__).parent / "web"


@lru_cache(maxsize=1)
def _template() -> str:
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


def render_page(page: str, request: Request) -> str:
    ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")
    base_href = f"{ingress_path}/" if re.fullmatch(r"/api/hassio_ingress/[A-Za-z0-9_-]+", ingress_path) else "/"
    return (
        _template()
        .replace("__BASE_HREF__", escape(base_href, quote=True))
        .replace("__PAGE__", escape(page, quote=True))
        .replace("__VERSION__", escape(ADDON_VERSION, quote=True))
    )


def asset_path(name: str) -> tuple[Path, str]:
    media_types = {"app.js": "text/javascript", "style.css": "text/css"}
    if name not in media_types:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return WEB_DIR / name, media_types[name]
