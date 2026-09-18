# -*- coding: utf-8 -*-
"""MariaDB 접속 공용 모듈 (.env 만 읽는다).

자격증명은 코드·문서에 쓰지 않는다 (CLAUDE.md 절대규칙). 이 모듈은 환경변수와
저장소 루트의 `.env` 만 본다. 값이 없으면 접속을 시도하지 않고 무엇이 비었는지 알려준다.

    from db import connect, query, datasets
    df = query("SELECT COUNT(*) AS n FROM fire_incident_information_std12")
    data = datasets()            # 학습 데이터셋 3종 (parquet 캐시 우선)

명령줄:
    .venv/bin/python scripts/db.py --check              # 접속 확인 + 서버/DB 정보
    .venv/bin/python scripts/db.py --tables             # 테이블 목록과 행수
    .venv/bin/python scripts/db.py --sql "SELECT 1"     # 임의 질의 (읽기 전용 용도)
    .venv/bin/python scripts/db.py --refresh-cache      # DB 재조회 -> data/cache/*.parquet
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
ENV_FILE = ROOT / ".env"

REQUIRED = ["FIRE_DB_HOST", "FIRE_DB_USER", "FIRE_DB_PASS", "FIRE_DB_NAME"]


def load_env(path: Path = ENV_FILE, override: bool = False) -> dict:
    """`.env` 를 읽어 os.environ 에 넣는다. 이미 있는 값은 건드리지 않는다(override=False)."""
    found = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not k:
            continue
        found[k] = v
        if override or k not in os.environ:
            os.environ[k] = v
    return found


def config(*, strict: bool = True) -> dict:
    """pymysql 접속 인자. 값은 환경변수(.env 포함)에서만 온다."""
    load_env()
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing and strict:
        raise RuntimeError(
            "DB 자격증명이 없다: " + ", ".join(missing) +
            f"\n  {ENV_FILE} 를 만들고 값을 채운다 (.env.example 참고). "
            "코드에 비밀번호를 적지 않는다.")
    return dict(
        host=os.getenv("FIRE_DB_HOST", ""),
        port=int(os.getenv("FIRE_DB_PORT", "3306")),
        user=os.getenv("FIRE_DB_USER", ""),
        password=os.getenv("FIRE_DB_PASS", ""),
        database=os.getenv("FIRE_DB_NAME", ""),
        charset="utf8mb4", connect_timeout=15, read_timeout=900,
    )


@contextmanager
def connect(**overrides):
    """pymysql 연결 컨텍스트. 사용 후 반드시 닫는다."""
    import pymysql
    cfg = config()
    cfg.update(overrides)
    conn = pymysql.connect(**cfg)
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params=None, **overrides) -> pd.DataFrame:
    """SELECT 를 DataFrame 으로 받는다 (pandas 의 SQLAlchemy 경고를 피해 커서로 읽는다)."""
    with connect(**overrides) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)


def ping(verbose: bool = True) -> bool:
    """접속 가능 여부를 확인한다. 실패해도 예외를 밖으로 던지지 않는다."""
    try:
        cfg = config()
    except RuntimeError as e:
        if verbose:
            print(f"✗ {e}")
        return False
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION(), DATABASE(), NOW()")
                ver, dbname, now = cur.fetchone()
                cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema = %s", (cfg["database"],))
                ntab = cur.fetchone()[0]
        if verbose:
            print(f"✓ 접속 성공  {cfg['user']}@{cfg['host']}:{cfg['port']}/{dbname}")
            print(f"  서버 {ver} · 서버시각 {now} · 테이블 {ntab}개")
        return True
    except Exception as e:                      # 네트워크·권한·타임아웃 전부 여기로
        if verbose:
            print(f"✗ 접속 실패 ({type(e).__name__}): {e}")
            print(f"  확인: {ENV_FILE} 의 FIRE_DB_* 값 · 방화벽 · "
                  f"{cfg['host']}:{cfg['port']} 도달 여부")
        return False


def tables() -> pd.DataFrame:
    cfg = config()
    return query(
        "SELECT table_name AS 테이블, table_rows AS 대략행수, "
        "ROUND((data_length+index_length)/1048576, 1) AS MB "
        "FROM information_schema.tables WHERE table_schema = %s "
        "ORDER BY table_rows DESC", params=(cfg["database"],))


def datasets(refresh: bool = False, verbose: bool = True) -> dict:
    """학습 데이터셋 3종. 캐시가 있으면 DB 를 건드리지 않는다.

    refresh=True 일 때만 DB 를 재조회하고 `data/cache/*.parquet` 를 다시 쓴다.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import fire_db
    return fire_db.load(db=config(strict=refresh), cache_dir=CACHE,
                        refresh=refresh, verbose=verbose)


def region_clusters() -> pd.Series | None:
    """지역 군집 정본 `ref_region_cluster`. 접속 못 하면 None (호출부에서 재적합)."""
    try:
        t = query("SELECT region_cd, cluster_id FROM ref_region_cluster")
        return t.set_index("region_cd")["cluster_id"].astype(int)
    except Exception as e:
        print(f"  ref_region_cluster 읽기 실패 ({type(e).__name__}) — 호출부 폴백")
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MariaDB 접속 도우미")
    ap.add_argument("--check", action="store_true", help="접속 확인")
    ap.add_argument("--tables", action="store_true", help="테이블 목록")
    ap.add_argument("--sql", help="임의 SELECT 실행")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="DB 를 다시 조회해 data/cache/*.parquet 갱신")
    a = ap.parse_args(argv)

    if not any([a.check, a.tables, a.sql, a.refresh_cache]):
        a.check = True
    if a.check and not ping():
        return 1
    if a.tables:
        print(tables().to_string(index=False))
    if a.sql:
        print(query(a.sql).to_string(index=False))
    if a.refresh_cache:
        d = datasets(refresh=True)
        for g in ("occurrence", "spread", "cause"):
            n = sum(len(v) for v in d[g].values())
            print(f"  {g:11s} {n:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
