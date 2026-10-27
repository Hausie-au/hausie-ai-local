"""The log UI must remain bounded on a Raspberry Pi."""

from datetime import datetime, timedelta, timezone
import gzip
import json
import logging

from hausie_ai.app.log_archive import LogArchive, MAX_MESSAGE_CHARS


def test_rotating_archive_query_and_gzip_export(tmp_path):
    archive = LogArchive(tmp_path, file_bytes=1_024, backups=2)
    handler = archive.handler()
    logger = logging.getLogger("hausie_ai.test.archive")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        for index in range(50):
            logger.info("EVENT entity=light.test_%s state=off->on", index)
        logger.warning("COLLECTOR failed entity=light.special")
        assert archive.retained_bytes() <= archive.max_bytes
        assert len(list(archive.path.parent.iterdir())) <= 3
        first = archive.query(limit=3)
        assert first["has_more"] is True
        assert first["entries"][0]["level"] == "WARNING"
        assert first["entries"][0]["category"] == "COLLECTOR"
        assert first["entries"][1]["message"].startswith("EVENT entity=light.test_49")
        second = archive.query(limit=3, offset=3)
        assert second["entries"][0] != first["entries"][0]
        filtered = archive.query(level="WARNING", category="COLLECT", search="special")
        assert len(filtered["entries"]) == 1
        compressed = b"".join(archive.download_gzip(level="WARNING"))
        exported = [json.loads(line) for line in gzip.decompress(compressed).splitlines()]
        assert len(exported) == 1
        assert exported[0]["message"] == "COLLECTOR failed entity=light.special"
        assert not list(archive.path.parent.glob("*.gz"))  # Export created no Pi-side archive.
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)
        logger.propagate = True


def test_archive_date_filters_and_oversized_messages(tmp_path):
    archive = LogArchive(tmp_path)
    handler = archive.handler()
    try:
        before = datetime.now(timezone.utc) - timedelta(seconds=10)
        record = logging.LogRecord("hausie_ai.test", logging.INFO, __file__, 1,
                                   "LEARN %s", ("x" * 20_000,), None)
        handler.handle(record)
        after = datetime.now(timezone.utc) + timedelta(seconds=10)
        entry = archive.query()["entries"][0]
        assert entry["category"] == "LEARN"
        assert len(entry["message"]) == MAX_MESSAGE_CHARS
        assert len(archive.query(since=before, until=after)["entries"]) == 1
        assert archive.query(until=before)["entries"] == []
        assert archive.query(since=after)["entries"] == []
        assert gzip.decompress(b"".join(archive.download_gzip(search="no match"))) == b""
    finally:
        handler.close()


def test_archive_survives_handler_restart_and_reverse_chunk_boundary(tmp_path):
    archive = LogArchive(tmp_path, file_bytes=256 * 1024, backups=1)
    first = archive.handler()
    try:
        for index in range(500):
            record = logging.LogRecord("hausie_ai.test", logging.INFO, __file__, 1,
                                       "EVENT number=%s %s", (index, "a" * 100), None)
            first.handle(record)
    finally:
        first.close()
    second = archive.handler()
    try:
        second.handle(logging.LogRecord("hausie_ai.test", logging.WARNING, __file__, 1,
                                        "HISTORY_BOOTSTRAP resumed", (), None))
        newest = archive.query(limit=2)["entries"]
        assert newest[0]["message"] == "HISTORY_BOOTSTRAP resumed"
        assert newest[1]["message"].startswith("EVENT number=499 ")
        assert archive.query(limit=1, offset=500)["entries"][0]["message"].startswith("EVENT number=0 ")
    finally:
        second.close()
