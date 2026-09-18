"""DB 접근 헬퍼 — pymysql 커넥션과 마이그레이션 러너."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymysql

from ..config import ROOT, db_config


RETRIES = 4
RETRY_BACKOFF = 1.5   # 초


def connect(**over) -> pymysql.connections.Connection:
    """원격 DB 라 일시적 연결 끊김(2013)이 발생한다. 지수 백오프로 재시도한다."""
    cfg = db_config()
    cfg.update(over)
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            return pymysql.connect(**cfg, autocommit=False, connect_timeout=15,
                                   read_timeout=600, write_timeout=600)
        except pymysql.err.OperationalError as e:
            last = e
            if attempt == RETRIES - 1:
                break
            time.sleep(RETRY_BACKOFF * (2 ** attempt))
    raise RuntimeError(
        f"DB 연결 실패 ({RETRIES}회 시도): {last}. "
        f"호스트 {cfg['host']}:{cfg['port']} 도달 가능 여부를 확인하라."
    ) from last


_SHARED: pymysql.connections.Connection | None = None


def shared() -> pymysql.connections.Connection:
    """모듈 단위 재사용 연결.

    호출마다 새 연결을 열면 배치가 수백 개를 소모해 원격 서버의 연결 한도를 친다
    (실측: 2026-09-01 배치 중 Errno 61 Connection refused).
    ping(reconnect=True) 로 살아있는 연결을 재사용하고, 끊겼으면 다시 연결한다.
    """
    global _SHARED
    if _SHARED is not None:
        try:
            _SHARED.ping(reconnect=True)
            return _SHARED
        except Exception:
            try:
                _SHARED.close()
            except Exception:
                pass
            _SHARED = None
    _SHARED = connect()
    return _SHARED


def close_shared() -> None:
    global _SHARED
    if _SHARED is not None:
        try:
            _SHARED.close()
        finally:
            _SHARED = None


def query(sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    c = shared()
    with c.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, params) if params else cur.execute(sql)
        return list(cur.fetchall())   # pymysql 은 tuple 을 준다 — 호출부 비교를 단순하게


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    c = shared()
    with c.cursor() as cur:
        n = cur.execute(sql, params) if params else cur.execute(sql)
        c.commit()
        return n


def executemany(sql: str, rows: Iterable[Sequence[Any]], batch: int = 2000) -> int:
    rows = list(rows)
    total = 0
    c = shared()
    with c.cursor() as cur:
        for i in range(0, len(rows), batch):
            total += cur.executemany(sql, rows[i:i + batch])
        c.commit()
    return total


def _split_statements(sql: str) -> list[str]:
    body = re.sub(r"^\s*--.*$", "", sql, flags=re.M)
    return [s.strip() for s in body.split(";") if s.strip()]


def run_migration(path: str | Path) -> int:
    """SQL 파일의 문장을 순서대로 실행한다."""
    stmts = _split_statements(Path(path).read_text(encoding="utf-8"))
    with connect() as c, c.cursor() as cur:
        for s in stmts:
            cur.execute(s)
        c.commit()
    return len(stmts)


def ensure_incident_unique_key() -> str:
    """fire_incident_information_std12.id 에 UNIQUE 키를 건다.

    현재 id 인덱스는 non-unique 라 `INSERT ... ON DUPLICATE KEY UPDATE` 가 충돌을
    검출하지 못한다. 그 상태로 패치 스크립트를 재실행하면 115,237행이 통째로
    추가되어 v_ml_* 뷰의 화재건수가 조용히 두 배가 된다.
    """
    tbl = "fire_incident_information_std12"
    with connect() as c, c.cursor() as cur:
        cur.execute(f"SHOW KEYS FROM `{tbl}` WHERE Column_name='id' AND Non_unique=0")
        if cur.fetchone():
            return "이미 UNIQUE"
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT id) FROM `{tbl}`")
        total, distinct = cur.fetchone()
        if total != distinct:
            return f"중복 존재로 보류 (총 {total:,} / 고유 {distinct:,}) — 먼저 중복 정리 필요"
        cur.execute(f"ALTER TABLE `{tbl}` ADD UNIQUE KEY uq_incident_id (id)")
        c.commit()
        return f"UNIQUE 키 생성 완료 ({total:,}행)"


def migrate_all() -> None:
    for f in sorted((ROOT / "sql").glob("0*.sql")):
        if f.name.startswith("001") or "fire_ml_views" in f.name:
            continue
        n = run_migration(f)
        print(f"  ✓ {f.name}: {n} statements")
    print(f"  ✓ incident UNIQUE key: {ensure_incident_unique_key()}")


if __name__ == "__main__":
    migrate_all()
