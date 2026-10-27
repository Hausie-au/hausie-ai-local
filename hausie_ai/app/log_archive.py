"""Bounded, local runtime logs for the Ingress viewer and streamed export.

The Supervisor log remains the source for container messages emitted before
Python starts. This archive captures Python logging from this version onward.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Any, Iterator
import zlib


LOG_FILE_BYTES = 4 * 1024 * 1024
LOG_BACKUPS = 4
MAX_MESSAGE_CHARS = 8_000
_CATEGORY = re.compile(r"^([A-Z][A-Z0-9_]+)(?:\b|$)")


class JsonLineFormatter(logging.Formatter):
    """One bounded JSON object per line, including tracebacks when present."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            message += "\n" + record.stack_info
        category_match = _CATEGORY.match(message)
        category = category_match.group(1) if category_match else "SERVER" if record.name.startswith("uvicorn") else "OTHER"
        return json.dumps({
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "category": category,
            "logger": record.name,
            "message": message[:MAX_MESSAGE_CHARS],
        }, ensure_ascii=False, separators=(",", ":"))


class OmitLogViewerRequests(logging.Filter):
    """Avoid self-matching search URLs and recursive log-viewer noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.name in {"uvicorn.access", "httpx"} and
                    "/api/v1/logs" in record.getMessage())


class LogArchive:
    def __init__(self, data_dir: Path, file_bytes: int = LOG_FILE_BYTES,
                 backups: int = LOG_BACKUPS):
        self.path = data_dir / "logs" / "hausie-ai.jsonl"
        self.file_bytes = file_bytes
        self.backups = backups

    @property
    def max_bytes(self) -> int:
        return self.file_bytes * (self.backups + 1)

    def handler(self) -> RotatingFileHandler:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(self.path, maxBytes=self.file_bytes,
                                      backupCount=self.backups, encoding="utf-8")
        handler.setFormatter(JsonLineFormatter())
        handler.addFilter(OmitLogViewerRequests())
        return handler

    def _paths(self, newest_first: bool) -> list[Path]:
        paths = [self.path] + [Path(f"{self.path}.{index}") for index in range(1, self.backups + 1)]
        return [path for path in (paths if newest_first else reversed(paths)) if path.is_file()]

    def retained_bytes(self) -> int:
        size = 0
        for path in self._paths(newest_first=True):
            try:
                size += path.stat().st_size
            except FileNotFoundError:
                pass  # A concurrent rollover removed this backup.
        return size

    @staticmethod
    def _reverse_lines(path: Path) -> Iterator[bytes]:
        """Read backwards in small chunks; never load an entire rotated file."""
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            remainder = b""
            while position:
                size = min(position, 64 * 1024)
                position -= size
                handle.seek(position)
                parts = (handle.read(size) + remainder).split(b"\n")
                remainder = parts[0]
                for line in reversed(parts[1:]):
                    if line:
                        yield line
            if remainder:
                yield remainder

    @staticmethod
    def _matches(entry: dict[str, Any], *, level: str = "", category: str = "",
                 search: str = "", since: datetime | None = None,
                 until: datetime | None = None) -> bool:
        if level and entry.get("level") != level.upper():
            return False
        if category and category.casefold() not in str(entry.get("category", "")).casefold():
            return False
        if search and search.casefold() not in " ".join(str(entry.get(key, "")) for key in
                                                      ("message", "logger", "category")).casefold():
            return False
        if since or until:
            try:
                at = datetime.fromisoformat(str(entry["time"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                return False
            if since and at < since:
                return False
            if until and at > until:
                return False
        return True

    def query(self, *, limit: int = 100, offset: int = 0, level: str = "",
              category: str = "", search: str = "", since: datetime | None = None,
              until: datetime | None = None) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        skipped = 0
        for path in self._paths(newest_first=True):
            try:
                for line in self._reverse_lines(path):
                    try:
                        entry = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue  # A concurrently written final line is not yet a log record.
                    if not self._matches(entry, level=level, category=category, search=search,
                                         since=since, until=until):
                        continue
                    if skipped < offset:
                        skipped += 1
                        continue
                    entries.append(entry)
                    if len(entries) > limit:
                        return {"entries": entries[:limit], "has_more": True,
                                "offset": offset, "retained_bytes": self.retained_bytes(),
                                "max_bytes": self.max_bytes}
            except FileNotFoundError:
                continue  # Rotation may rename a file between listing and opening.
        return {"entries": entries, "has_more": False, "offset": offset,
                "retained_bytes": self.retained_bytes(), "max_bytes": self.max_bytes}

    def download_gzip(self, *, level: str = "", category: str = "", search: str = "",
                      since: datetime | None = None,
                      until: datetime | None = None) -> Iterator[bytes]:
        """Stream filtered retained JSONL as gzip, without a temporary archive."""
        compressor = zlib.compressobj(level=6, wbits=31)
        for path in self._paths(newest_first=False):
            try:
                with path.open("rb") as handle:
                    for line in handle:
                        try:
                            entry = json.loads(line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if self._matches(entry, level=level, category=category, search=search,
                                         since=since, until=until):
                            block = compressor.compress(line)
                            if block:
                                yield block
            except FileNotFoundError:
                continue
        yield compressor.flush()
