# -*- coding: utf-8 -*-
"""현재 행정구역 기준 지역 마스터 생성.

낡은 표기 3종을 현재 기준으로 정리한다 (원천 테이블은 건드리지 않는다).
  1. 강원도       -> 강원특별자치도   (2023-06-11 시행)
  2. 전라북도     -> 전북특별자치도   (2024-01-18 시행)
  3. 경상북도|군위군 -> 대구광역시|군위군 (2023-07-01 편입)

산출 (신규 테이블 2개, 기존 테이블 무변경)
  ref_region_current : 현재 기준 지역 마스터 250행
  ref_region_alias   : 원천/보조 테이블 조인 매핑 (표기 세대 차이 흡수)

실행
  python scripts/region_normalize.py            # 미리보기만 (DB 쓰기 없음)
  python scripts/region_normalize.py --apply    # 신규 테이블 2개 생성·적재
"""
from __future__ import annotations
import sys, re
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fire_db

# ---------------------------------------------------------------- 개편 규칙
SIDO_RENAME = {"강원도": "강원특별자치도", "전라북도": "전북특별자치도"}
SIDO_RENAME_DATE = {"강원특별자치도": "2023-06-11", "전북특별자치도": "2024-01-18"}
MERGE = {("경상북도", "군위군"): ("대구광역시", "군위군", "2023-07-01")}
NAME_FIX = {("세종특별자치시", "세종"): "세종특별자치시",       # 단층제 — 시군구 없음
            ("세종특별자치시", "세종시"): "세종특별자치시"}

def to_current(sido: str, sgg: str) -> tuple[str, str]:
    """임의 표기 -> 현재 기준 (시도, 시군구)."""
    sido = SIDO_RENAME.get(str(sido).strip(), str(sido).strip())
    sgg = str(sgg).strip()
    if (sido, sgg) in MERGE:
        sido, sgg, _ = MERGE[(sido, sgg)]
    return sido, sgg

_ns = lambda v: re.sub(r"\s+", "", str(v).strip())     # 공백 무시 비교키

_GU = re.compile(r"^(.+?시)\s*(.+구)$")
def region_type(sido: str, sgg: str) -> tuple[str, str | None]:
    if sgg == "세종": return "특별자치시", None
    광역 = ("광역시" in sido) or ("특별시" in sido)
    m = _GU.match(sgg)
    if m and not 광역: return "일반구", m.group(1)
    if sgg.endswith("구"): return "자치구", None
    if sgg.endswith("군"): return "군", None
    return "시", None

def _igmj():
    """인구밀도 테이블은 fire_db 가 안 읽으므로 여기서 직접 조회."""
    import pymysql
    with pymysql.connect(**fire_db.DB_DEFAULT) as c:
        return pd.read_sql("SELECT 시도명, 시군구명 FROM `igmj_abc17.csv_std17`", c)


# ---------------------------------------------------------------- 마스터 구성
def build():
    raw = fire_db.fetch_raw(verbose=False)
    hist = raw["sgg"].copy()                       # 원천 화재 기준 251행 (역사 표기)
    hist["cur_sido"], hist["cur_sgg"] = zip(*[to_current(a, b) for a, b in zip(hist["시도"], hist["시군구"])])
    hist["region_cd"] = fire_db.region_cd(hist["cur_sido"], hist["cur_sgg"])
    hist["hist_cd"] = fire_db.region_cd(hist["시도"], hist["시군구"])

    cur = hist.drop_duplicates("region_cd")[["region_cd", "cur_sido", "cur_sgg"]].copy()
    cur.columns = ["region_cd", "sido", "sigungu"]
    cur[["region_type", "parent_si"]] = [region_type(a, b) for a, b in zip(cur.sido, cur.sigungu)]
    cur = cur.sort_values("region_cd").reset_index(drop=True)

    # ---- 보조 테이블 조인 매핑 ----
    # 표기 비교는 공백을 무시한다.
    #  화재 원자료가 일반구 표기에 공백을 일관되게 쓰지 않는다:
    #    경기도  '고양시덕양구'   (공백 없음)
    #    경상남도 '창원시 마산합포구' (공백 있음)
    #  보조 테이블(igmj)은 전부 공백 있음. 공백을 무시해야 같은 지역으로 붙는다.
    def keyset(df, c1, c2):
        out = {}
        for a, b in zip(df[c1], df[c2]):
            out[(_ns(a), _ns(b))] = (str(a).strip(), str(b).strip())
        return out
    pop = raw["pop"].copy()
    pop["시도"] = pop["행정구역"].str.split().str[0]
    pop["시군구"] = pop["행정구역"].str.split().str[1:].str.join(" ")
    SRC = {"fire":       keyset(hist, "시도", "시군구"),
           "forest":     keyset(raw["forest"], "시도", "세부행정구역"),
           "popdensity": keyset(_igmj(), "시도명", "시군구명"),
           "population": keyset(pop, "시도", "시군구")}

    rows = []
    for _, r in cur.iterrows():
        for src, keys in SRC.items():
            cands = []
            def hit(sido, sgg, level):
                k = (_ns(sido), _ns(sgg))
                if k in keys:
                    js, jg = keys[k]; cands.append((js, jg, level))
            hit(r.sido, r.sigungu, "exact")                                   # 현재 표기
            for old, new in SIDO_RENAME.items():                              # 구 시도 표기
                if new == r.sido: hit(old, r.sigungu, "renamed")
            for (osido, osgg), (nsido, nsgg, _d) in MERGE.items():            # 편입 전 소속
                if (nsido, nsgg) == (r.sido, r.sigungu): hit(osido, osgg, "merged")
            if r.parent_si:                                                   # 일반구 -> 부모 시
                for s2 in {r.sido} | {o for o, n in SIDO_RENAME.items() if n == r.sido}:
                    hit(s2, r.parent_si, "parent_city")
            if r.sigungu == "세종":                                            # 세종 표기 변형
                for s2 in (r.sido,):
                    hit(s2, "세종시", "alias"); hit(s2, "세종", "alias")
            for js, jg, lv in cands:
                rows.append(dict(region_cd=r.region_cd, source=src, join_sido=js, join_sgg=jg,
                                 match_level=lv,
                                 valid_from=(SIDO_RENAME_DATE.get(r.sido) if lv == "renamed" else
                                             (MERGE.get((js, jg), (None, None, None))[2] if lv == "merged" else None))))
    alias = pd.DataFrame(rows).drop_duplicates(["region_cd","source","join_sido","join_sgg"])
    PRI = {"exact":0, "merged":1, "renamed":2, "alias":3, "parent_city":4}
    alias["_p"] = alias["match_level"].map(PRI)
    alias = (alias.sort_values(["region_cd","source","_p"])
                  .drop_duplicates(["region_cd","source"]).drop(columns="_p")
                  .reset_index(drop=True))
    return hist, cur, alias

def main(apply=False):
    hist, cur, alias = build()
    print("="*66); print("현재 행정구역 기준 지역 마스터"); print("="*66)
    print(f"  역사 표기 키 {hist['hist_cd'].nunique()}  ->  현재 기준 {len(cur)}")
    diff = hist[hist["hist_cd"] != hist["region_cd"]][["hist_cd","region_cd"]].drop_duplicates()
    print(f"  변경된 키 {len(diff)}건:")
    for _, r in diff.iterrows(): print(f"    {r.hist_cd:26s} -> {r.region_cd}")
    print("\n  유형 구성:", cur["region_type"].value_counts().to_dict())
    print("\n  보조 테이블 조인 커버리지:")
    for src in ("fire","forest","popdensity","population"):
        a = alias[alias["source"] == src]
        cov = a["region_cd"].nunique()
        lv = a["match_level"].value_counts().to_dict()
        flag = "" if cov == len(cur) else f"   <-- {len(cur)-cov}개 미매칭"
        print(f"    {src:11s} {cov:>3}/{len(cur)}  {lv}{flag}")
    if not apply:
        print("\n  (미리보기) --apply 를 붙이면 신규 테이블 2개를 생성·적재한다. 기존 테이블은 건드리지 않는다.")
        return cur, alias
    import pymysql
    DDL_CUR = """
    CREATE TABLE IF NOT EXISTS ref_region_current (
      region_cd   VARCHAR(60)  NOT NULL COMMENT '현재 기준 정본 (시도|시군구, 공백 유지)',
      sido        VARCHAR(20)  NOT NULL,
      sigungu     VARCHAR(40)  NOT NULL,
      region_type VARCHAR(12)  NOT NULL COMMENT '자치구|일반구|시|군|특별자치시',
      parent_si   VARCHAR(40)  NULL     COMMENT '일반구의 부모 시',
      PRIMARY KEY (region_cd), KEY idx_sido (sido)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='현재 행정구역 기준 지역 마스터'"""
    DDL_ALIAS = """
    CREATE TABLE IF NOT EXISTS ref_region_alias (
      region_cd   VARCHAR(60) NOT NULL,
      source      VARCHAR(20) NOT NULL COMMENT 'fire|forest|popdensity|population',
      join_sido   VARCHAR(20) NOT NULL COMMENT '그 테이블에서 쓰는 시도 표기',
      join_sgg    VARCHAR(40) NOT NULL COMMENT '그 테이블에서 쓰는 시군구 표기',
      match_level VARCHAR(12) NOT NULL COMMENT 'exact|renamed|merged|parent_city|alias',
      valid_from  DATE        NULL     COMMENT '개편 시행일',
      PRIMARY KEY (region_cd, source, join_sido, join_sgg), KEY idx_src (source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='표기 세대 차이 흡수용 조인 매핑'"""
    with pymysql.connect(**fire_db.DB_DEFAULT) as c:
        cu = c.cursor()
        cu.execute(DDL_CUR); cu.execute(DDL_ALIAS)
        cu.execute("DELETE FROM ref_region_current"); cu.execute("DELETE FROM ref_region_alias")
        cu.executemany("INSERT INTO ref_region_current VALUES (%s,%s,%s,%s,%s)",
                       cur[["region_cd","sido","sigungu","region_type","parent_si"]]
                       .where(pd.notna(cur), None).values.tolist())
        cu.executemany("INSERT INTO ref_region_alias VALUES (%s,%s,%s,%s,%s,%s)",
                       alias[["region_cd","source","join_sido","join_sgg","match_level","valid_from"]]
                       .where(pd.notna(alias), None).values.tolist())
        c.commit()
    print(f"\n  적재 완료: ref_region_current {len(cur)}행 · ref_region_alias {len(alias)}행")
    print("  기존 테이블(fire_incident_*, ref_region_profile, pred_daily 등)은 변경하지 않았다.")
    return cur, alias

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
