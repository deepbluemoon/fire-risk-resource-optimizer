"""환경 설정 — .env 로더와 경로 상수.

자격증명을 코드에 두지 않는다. .env 또는 FIRE_DB_* 환경변수에서만 읽는다.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "datasets"
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"

# 분할 경계 (plan.md §D-2) — 전 모델 공통
DEV_START, DEV_END = "2021-01-01", "2022-12-31"      # 개발셋 24개월
HOLDOUT_START, HOLDOUT_END = "2023-01-01", "2023-12-31"  # 최종 1회만 개봉

# 2026-09-04 — 251 → 250.
# 군위군이 2023-07 대구 편입으로 경북·대구 두 행으로 중복돼 있었고, 그중 하나가
# 참조 테이블에서 제거되면서 마스터가 250행이 됐다. 이 상수는 '참조 테이블이
# 덜 적재된 상태'를 잡는 방어선이므로, 실제 지역 수에 맞춰 함께 내린다.
N_REGIONS = 250


def load_env(path: str | Path = None) -> None:
    """.env 를 os.environ 으로 로드한다. 이미 설정된 값은 덮어쓰지 않는다."""
    p = Path(path) if path else ROOT / ".env"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            key = key.strip()
            if key and not key.startswith("#") and key not in os.environ:
                os.environ[key] = val.strip().strip("\"'")
    except FileNotFoundError:
        pass


def db_config() -> dict:
    load_env()
    missing = [k for k in ("FIRE_DB_HOST", "FIRE_DB_USER", "FIRE_DB_PASS", "FIRE_DB_NAME")
               if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"DB 접속정보가 없습니다: {', '.join(missing)}. "
            ".env 를 만들거나 환경변수를 설정하세요 (.env.example 참조)."
        )
    return dict(
        host=os.environ["FIRE_DB_HOST"],
        port=int(os.environ.get("FIRE_DB_PORT", 13306)),
        user=os.environ["FIRE_DB_USER"],
        password=os.environ["FIRE_DB_PASS"],
        db=os.environ["FIRE_DB_NAME"],
        charset="utf8mb4",
    )


def kma_api_key() -> str | None:
    load_env()
    return os.environ.get("KMA_API_KEY") or None
