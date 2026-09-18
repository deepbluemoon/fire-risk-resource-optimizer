"""구조화 로깅 — JSON 라인. 배치 실행 추적용."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime

from .config import LOGS_DIR


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in getattr(record, "extra_fields", {}).items():
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str, to_file: bool = True) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(JsonLineFormatter())
    log.addHandler(sh)
    if to_file:
        LOGS_DIR.mkdir(exist_ok=True)
        fh = logging.FileHandler(LOGS_DIR / "batch.log", encoding="utf-8")
        fh.setFormatter(JsonLineFormatter())
        log.addHandler(fh)
    return log
