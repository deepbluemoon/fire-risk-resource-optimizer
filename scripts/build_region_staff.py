"""지역별 소방인력 정규화 — 251개 시군구에 1행씩.

원자료 `ref_sgg_fire_staff`(229행)는 예측 지역(251곳)과 세 가지가 어긋난다.

  1. 시도 표기      '강원' vs '강원특별자치도' (강원·전북·제주 3건)
  2. 세종 표기      '세종시' vs '세종'
  3. 행정구 분할    원자료는 '수원시' 한 줄인데 예측은 4개 구로 나뉜다 (12개 시 33개 구)

여기에 더해 `군위군`이 251곳 안에 경북·대구 양쪽으로 들어 있다(2023-07 대구 편입).
학습 구간(2021~2023) 대부분이 경북 소속이었으므로 **경북에 싣고 대구 쪽은 0으로 둔다.**
행을 지우지 않는 이유는, 251곳 어디서 조인해도 실패하지 않게 하기 위해서다.

구 배분은 **인구 비례**로 한다. 면적은 쓰지 않는다 — 원자료의 구 면적이 시 면적을
그대로 복제한 값이라(창원 5개 구가 각각 748.1km²) 가중치로 쓸 수 없다.

    실행:  .venv/bin/python scripts/build_region_staff.py [--write]
           --write 없이는 검증만 하고 아무것도 저장하지 않는다.
"""
import os
import pathlib
import sys
from collections import defaultdict

import pymysql

ROOT = pathlib.Path(__file__).resolve().parent.parent

SIDO_FIX = {"강원": "강원특별자치도", "전북": "전북특별자치도", "제주": "제주특별자치도"}
SIGUNGU_FIX = {("세종특별자치시", "세종시"): "세종"}
# 251곳 안에 두 번 있는 지역 — 인력을 실을 쪽과 0으로 둘 쪽
DUP_KEEP = "경상북도|군위군"
DUP_ZERO = "대구광역시|군위군"
DUP_SOURCE = "대구광역시|군위군"          # 원자료가 인력을 달고 있는 키


def connect():
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return pymysql.connect(
        host=os.getenv("FIRE_DB_HOST"), port=int(os.getenv("FIRE_DB_PORT")),
        user=os.getenv("FIRE_DB_USER"), password=os.getenv("FIRE_DB_PASS"),
        database=os.getenv("FIRE_DB_NAME"), charset="utf8mb4")


def normalize(sido: str, sigungu: str) -> str:
    sido = SIDO_FIX.get(sido, sido)
    sigungu = SIGUNGU_FIX.get((sido, sigungu), sigungu)
    return f"{sido}|{sigungu}"


def build(cur):
    cur.execute("SELECT sido, sigungu, staff_est FROM ref_sgg_fire_staff")
    src = {}
    for sido, sigungu, staff in cur.fetchall():
        src[normalize(sido, sigungu)] = float(staff)

    # 군위군을 사용자가 정한 소속(경북)으로 옮긴다
    moved = None
    if DUP_SOURCE in src:
        moved = src.pop(DUP_SOURCE)
        src[DUP_KEEP] = moved

    cur.execute("""SELECT region_cd, population FROM ref_region_profile
                   WHERE region_cd IN (SELECT DISTINCT region_cd FROM pred_daily)""")
    pop = {r: float(p) for r, p in cur.fetchall()}

    cur.execute("""SELECT DISTINCT region_cd FROM pred_daily
                   WHERE target_date = (SELECT MAX(target_date) FROM pred_daily)""")
    regions = sorted(r[0] for r in cur.fetchall())

    # 1) 그대로 맞는 지역
    out, unmatched = {}, []
    for rc in regions:
        if rc == DUP_ZERO:
            out[rc] = (0.0, "중복제외", DUP_KEEP)
        elif rc in src:
            out[rc] = (src[rc], "직접", None)
        else:
            unmatched.append(rc)

    # 2) 남은 것은 '시' 이름으로 시작하는지 보고 인구비로 나눈다
    children = defaultdict(list)
    for rc in unmatched:
        sido, sgg = rc.split("|", 1)
        cands = [k for k in src
                 if k.split("|", 1)[0] == sido and sgg.replace(" ", "").startswith(k.split("|", 1)[1])]
        if not cands:
            continue
        parent = max(cands, key=lambda k: len(k))     # 가장 긴(구체적인) 이름을 부모로
        children[parent].append(rc)

    for parent, kids in children.items():
        total_pop = sum(pop.get(k, 0.0) for k in kids)
        for k in kids:
            w = (pop.get(k, 0.0) / total_pop) if total_pop else (1.0 / len(kids))
            out[k] = (src[parent] * w, "구배분", parent)

    still = [r for r in regions if r not in out]
    return out, src, children, still, moved


def check(out, src, children, still, moved):
    ok = True

    def say(label, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  {'✓' if cond else '✗'} {label}{('  — ' + detail) if detail else ''}")

    print("\n1차 — 개수와 중복")
    say("251행 정확히", len(out) == 251, f"{len(out)}행")
    say("region_cd 중복 없음", len(out) == len(set(out)))
    say("누락 지역 없음", not still, f"누락 {still}" if still else "")

    print("\n2차 — 총량 보존")
    src_total = sum(src.values())
    out_total = sum(v[0] for v in out.values())
    say("배분 전후 인력 합계 일치", abs(src_total - out_total) < 1e-6,
        f"{src_total:,.2f} → {out_total:,.2f}")
    for parent, kids in children.items():
        s = sum(out[k][0] for k in kids)
        if abs(s - src[parent]) > 1e-6:
            say(f"{parent} 배분 합 불일치", False, f"{src[parent]:.2f} vs {s:.2f}")
    say("모든 시의 구 배분 합이 원값과 일치", True, f"{len(children)}개 시 검사")

    print("\n3차 — 값의 온전함")
    say("음수·결측 없음", all(v[0] >= 0 for v in out.values()))
    say("0인 곳은 중복제외 1건뿐",
        [k for k, v in out.items() if v[0] == 0] == [DUP_ZERO],
        str([k for k, v in out.items() if v[0] == 0]))
    say("군위군 인력이 경북으로 이동", moved is not None and out[DUP_KEEP][0] == moved,
        f"{moved}명 → {DUP_KEEP}" if moved else "원자료에 없음")
    return ok


def main():
    cn = connect(); cur = cn.cursor()
    out, src, children, still, moved = build(cur)

    print(f"원자료 {len(src)}개 키 · 구 배분 대상 {len(children)}개 시 "
          f"{sum(len(v) for v in children.values())}개 구")
    for parent, kids in sorted(children.items()):
        print(f"    {parent:22} → {len(kids)}개 구  "
              f"{src[parent]:8.2f}명 배분")

    ok = check(out, src, children, still, moved)
    print("\n" + ("모든 검증 통과" if ok else "검증 실패 — 저장하지 않는다"))
    if not ok:
        cn.close(); sys.exit(1)

    if "--write" not in sys.argv:
        print("(--write 를 주지 않아 저장하지 않았다)")
        cn.close(); return

    cur.execute("""CREATE TABLE IF NOT EXISTS ref_region_fire_staff (
        region_cd    VARCHAR(64)  NOT NULL PRIMARY KEY,
        sido         VARCHAR(32)  NOT NULL,
        sigungu      VARCHAR(64)  NOT NULL,
        staff_est    DECIMAL(10,2) NOT NULL,
        staff_source VARCHAR(16)  NOT NULL,
        parent_cd    VARCHAR(64)  NULL,
        population   INT          NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cur.execute("DELETE FROM ref_region_fire_staff")
    cur.execute("""SELECT region_cd, population FROM ref_region_profile""")
    pop = {r: int(p) for r, p in cur.fetchall()}
    rows = [(rc, rc.split("|", 1)[0], rc.split("|", 1)[1], round(v[0], 2), v[1], v[2], pop.get(rc))
            for rc, v in sorted(out.items())]
    cur.executemany("""INSERT INTO ref_region_fire_staff
        (region_cd,sido,sigungu,staff_est,staff_source,parent_cd,population)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""", rows)
    cn.commit()
    cur.execute("SELECT COUNT(*), SUM(staff_est) FROM ref_region_fire_staff")
    n, t = cur.fetchone()
    print(f"저장 완료 — ref_region_fire_staff {n}행 · 합계 {float(t):,.2f}명")
    cn.close()


if __name__ == "__main__":
    main()
