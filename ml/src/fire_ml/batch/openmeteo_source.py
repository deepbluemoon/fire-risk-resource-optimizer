"""Open-Meteo 기상 공급원 — 운영 예측의 기상 입력.

왜 이 모듈이 있나
  `features_source.py` 는 학습 데이터셋(2021~2023 관측)을 공급원으로 쓴다. 그
  구간 밖의 날짜에는 기상이 없어 M0 기저율로 폴백한다. 오늘 날짜는 그 구간
  밖이므로, 서비스가 내보내는 위험도는 사실상 지역×계절 평균이었다.
  이 모듈이 그 자리를 대신해 **실제 날씨로 예측**하게 한다.

설계
  - 좌표는 DB `tbl_sido_lonlat_std05` 의 시청·군청·구청 위치(251개)를 쓴다.
  - 한 번 호출로 과거 `LOOKBACK_DAYS` + 예보 `forecast_days` 를 함께 받는다.
    `실효습도`(5일 지수가중)·`무강수일수`(연속 건조일)는 과거 이력이 있어야
    계산되기 때문이다. 오늘 값만 받으면 이 두 A등급 피처를 만들 수 없다.
  - Open-Meteo 격자값은 기상청 관측소 값과 계통 편차가 있다. 보정계수는
    `models/openmeteo_bias.json` 에서 읽는다 (없으면 무보정 + 경고).

한도
  Open-Meteo 무료 티어는 호출 '횟수'가 아니라 데이터량(일수 × 변수)으로 센다.
  251지역 × 32일 × 7변수는 하루 한도 안에서 여유롭다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import MODELS_DIR
from ..data.db import query
from ..logging import get_logger

log = get_logger("openmeteo")

API = "https://api.open-meteo.com/v1/forecast"

# M1 이 실제로 쓰는 것만 받는다. 변수를 늘리면 그만큼 한도를 더 쓴다.
DAILY = ["temperature_2m_mean", "temperature_2m_max", "temperature_2m_min",
         "relative_humidity_2m_mean", "relative_humidity_2m_min",
         "wind_speed_10m_mean", "precipitation_sum"]

COLMAP = {"기온": "temperature_2m_mean", "최고기온": "temperature_2m_max",
          "최저기온": "temperature_2m_min", "습도": "relative_humidity_2m_mean",
          "최소습도": "relative_humidity_2m_min", "풍속": "wind_speed_10m_mean",
          "강수량": "precipitation_sum"}

# 실효습도 5일 지수가중 + 무강수일수 연속 계산에 필요한 과거 구간.
# 무강수일수는 이론상 무한히 길어질 수 있어 넉넉히 잡는다 (학습데이터 최댓값 55일).
LOOKBACK_DAYS = 60
# 예보는 받을 수 있는 만큼 한 번에 받아 둔다 (Open-Meteo 최대 16일).
# 대상일마다 다른 길이로 받으면 그때마다 251회를 다시 긁게 된다.
FORECAST_DAYS = 16
EFF_HUMIDITY_DECAY = 0.7
EFF_HUMIDITY_WINDOW = 5
DEFAULT_PRECIP_THRESHOLD_MM = 1.0    # scripts/fire_db.py 와 같은 정의

REQUEST_SLEEP_SEC = 0.4
MAX_RETRY = 5


# ------------------------------------------------------------------ 보정
@lru_cache(maxsize=1)
def bias() -> dict:
    """`models/openmeteo_bias.json` 을 읽는다. 없으면 무보정으로 동작하되 경고한다."""
    p = MODELS_DIR / "openmeteo_bias.json"
    if not p.exists():
        log.warning("openmeteo_bias.json 없음 — 무보정으로 동작한다. "
                    "풍속이 과대, 무강수일수가 과소 입력된다.")
        return {"wind_scale": 1.0, "precip_threshold_mm": DEFAULT_PRECIP_THRESHOLD_MM}
    d = json.loads(p.read_text(encoding="utf-8"))
    log.info(f"보정 적용: wind_scale={d.get('wind_scale')} "
             f"precip_threshold={d.get('precip_threshold_mm')}mm "
             f"({d.get('fitted_on', '출처미상')})")
    return d


# ------------------------------------------------------------------ 좌표
@lru_cache(maxsize=1)
def coords() -> pd.DataFrame:
    """251 시군구 → 청사 좌표. 기관명에서 '청' 을 떼어 시군구명과 맞춘다."""
    rows = query("""SELECT p.region_cd, p.sido, p.sigungu, p.forest_ratio, p.pop_density,
                           p.avg_station_distance_km, p.log_population, p.population,
                           p.area_km2
                    FROM ref_region_profile p""")
    reg = pd.DataFrame(rows)
    rows2 = query("SELECT 기관명, 위도, 경도 FROM tbl_sido_lonlat_std05")
    co = pd.DataFrame(rows2)
    co["k"] = (co["기관명"].astype(str).str.replace(" ", "", regex=False)
                 .str.replace(r"청$", "", regex=True))
    reg["k"] = reg["sigungu"].astype(str).str.replace(" ", "", regex=False)
    m = reg.merge(co.drop_duplicates("k")[["k", "위도", "경도"]], on="k", how="left")
    miss = int(m["위도"].isna().sum())
    if miss:
        raise RuntimeError(f"좌표 없는 시군구 {miss}개 — tbl_sido_lonlat_std05 를 먼저 채워라")
    return m.drop(columns="k")


# ------------------------------------------------------------------ 수집
def _get(lat: float, lon: float, past_days: int, forecast_days: int) -> pd.DataFrame | None:
    q = {"latitude": lat, "longitude": lon, "daily": ",".join(DAILY),
         "timezone": "Asia/Seoul", "wind_speed_unit": "ms",
         "past_days": past_days, "forecast_days": forecast_days}
    url = f"{API}?{urllib.parse.urlencode(q)}"
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                j = json.load(r)
            d = pd.DataFrame(j["daily"])
            d["날짜"] = pd.to_datetime(d.pop("time"))
            return d
        except urllib.error.HTTPError as e:
            if e.code == 429:                       # 한도는 실패가 아니라 대기 신호
                wait = 30 * (attempt + 1)
                log.warning(f"429 — {wait}s 대기 (시도 {attempt + 1}/{MAX_RETRY})")
                time.sleep(wait)
                continue
            log.warning(f"HTTP {e.code} (시도 {attempt + 1}/{MAX_RETRY})")
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            log.warning(f"{type(e).__name__} (시도 {attempt + 1}/{MAX_RETRY})")
            time.sleep(3 * (attempt + 1))
    return None


# ------------------------------------------------------------------ 파생
def _effective_humidity(h: np.ndarray) -> np.ndarray:
    """일평균습도의 지수가중 이동평균. scripts/fire_db.py 와 같은 정의."""
    w = np.array([EFF_HUMIDITY_DECAY ** n for n in range(EFF_HUMIDITY_WINDOW)])
    out = np.full(len(h), np.nan)
    for i in range(len(h)):
        seg = h[max(0, i - EFF_HUMIDITY_WINDOW + 1): i + 1][::-1]
        ww = w[:len(seg)]
        ok = ~np.isnan(seg)
        if ok.any():
            out[i] = float((ww[ok] * seg[ok]).sum() / ww[ok].sum())
    return out


def _dry_days(rain: np.ndarray, thr: float) -> np.ndarray:
    """강수 thr 이상이면 0 으로 리셋, 아니면 +1."""
    out = np.zeros(len(rain))
    d = 0
    for i, r in enumerate(rain):
        d = 0 if (not np.isnan(r) and r >= thr) else d + 1
        out[i] = d
    return out


def _derive(d: pd.DataFrame, b: dict) -> pd.DataFrame:
    """지역 1곳의 시계열에 보정과 파생을 적용한다. 날짜 오름차순 전제."""
    d = d.sort_values("날짜").reset_index(drop=True)
    for ko, en in COLMAP.items():
        d[ko] = pd.to_numeric(d[en], errors="coerce")

    d["풍속"] = d["풍속"] * float(b.get("wind_scale", 1.0))
    d["일교차"] = d["최고기온"] - d["최저기온"]
    d["실효습도"] = _effective_humidity(d["습도"].to_numpy(float))
    d["무강수일수"] = _dry_days(d["강수량"].to_numpy(float),
                             float(b.get("precip_threshold_mm", DEFAULT_PRECIP_THRESHOLD_MM)))
    d["실효습도_t1"] = d["실효습도"].shift(1)
    d["무강수일수_t1"] = d["무강수일수"].shift(1)

    # `build()` 는 월·연중일만 날짜에서 만들고 요일은 만들지 않는다. 학습 데이터의
    # 규약을 그대로 따라야 한다 — scripts/fire_db.py 는 JS getUTCDay 를 옮겨
    # **0=일요일** 로 쓴다. pandas dayofweek(0=월)를 그대로 넣으면 요일이 하루씩
    # 밀려 범주가 통째로 어긋난다.
    d["요일"] = (d["날짜"].dt.dayofweek + 1) % 7
    d["주말"] = d["요일"].isin([0, 6]).astype(int)
    d["월"] = d["날짜"].dt.month
    d["연중일"] = d["날짜"].dt.dayofyear
    return d


# ------------------------------------------------------------------ 공개 API
def available_range() -> tuple[date, date]:
    """Open-Meteo 가 덮는 구간. 과거는 아카이브, 미래는 예보 한계."""
    t = date.today()
    return t - timedelta(days=LOOKBACK_DAYS), t + timedelta(days=FORECAST_DAYS - 1)


@lru_cache(maxsize=1)
def panel() -> pd.DataFrame | None:
    """251지역 × (과거 LOOKBACK_DAYS + 예보 FORECAST_DAYS) 패널. 한 번만 받는다.

    **인자를 받지 않는 것이 핵심이다.** 예전에는 forecast_days 를 인자로 받고
    for_date 가 대상일마다 다른 값을 넘겼다. 그러면 캐시 키가 매번 달라져
    날짜 하나당 251회씩 다시 긁는다 — 7일 backfill 이 1,757회가 됐다.
    받을 수 있는 최대 구간을 한 번 받아 두고 날짜별로 잘라 쓴다.
    """
    forecast_days = FORECAST_DAYS
    reg = coords()
    b = bias()
    frames, failed = [], []
    for r in reg.itertuples(index=False):
        raw = _get(float(r.위도), float(r.경도), LOOKBACK_DAYS, forecast_days)
        if raw is None:
            failed.append(r.region_cd)
            continue
        d = _derive(raw, b)
        d["region_cd"] = r.region_cd
        d["시도"] = r.sido
        d["시군구"] = r.sigungu
        d["산림율"] = float(r.forest_ratio)
        d["인구밀도"] = float(r.pop_density)
        d["평균소방서거리"] = float(r.avg_station_distance_km)
        d["인구"] = r.population
        d["면적_km2"] = float(r.area_km2)
        d["log_인구"] = float(r.log_population)
        frames.append(d)
        time.sleep(REQUEST_SLEEP_SEC)

    if failed:
        log.warning(f"기상 수집 실패 {len(failed)}개 지역: {failed[:5]}…")
    if not frames:
        log.error("Open-Meteo 에서 한 지역도 못 받았다 — M0 폴백")
        return None

    df = _attach_static(pd.concat(frames, ignore_index=True))
    log.info(f"Open-Meteo 패널 {len(df):,}행 · 지역 {df['region_cd'].nunique()}개 "
             f"· {df['날짜'].min().date()}~{df['날짜'].max().date()} (실패 {len(failed)})")
    return df


def for_date(target: date) -> pd.DataFrame | None:
    """해당 일자의 251행 기상·지역 피처. 실패하면 None (→ run_daily 가 M0 로 폴백).

    `features_source.for_date` 와 같은 계약이다. run_daily 는 이 둘을 구분하지 않는다.
    """
    delta = (target - date.today()).days
    if delta > FORECAST_DAYS - 1:
        log.warning(f"{target} 는 예보 범위 밖 (+{delta}일)")
        return None
    if delta < -LOOKBACK_DAYS:
        log.warning(f"{target} 는 조회 범위 밖 ({delta}일)")
        return None

    p = panel()
    if p is None:
        return None
    d = p[p["날짜"] == pd.Timestamp(target)]
    if not len(d):
        log.warning(f"{target} 행이 패널에 없다")
        return None
    return d.reset_index(drop=True)


def _attach_static(df: pd.DataFrame) -> pd.DataFrame:
    """모델이 요구하는 정적·이력 열을 붙인다.

    `전년화재율` 은 화재 원자료가 2023-12-31 에서 끊겨 있어 당해 기준으로는
    만들 수 없다. 최신 확보 연도(2023)의 값을 고정해 쓴다. 화재율은 지역의
    안정적 특성이고, 결측으로 두면 M1 이 A등급 신호를 통째로 버린다.
    """
    rows = query("""SELECT a.region_cd,
                           SUM(a.fire_count) / NULLIF(p.population, 0) * 100000 AS 전년화재율
                    FROM ref_actual_daily a
                    JOIN ref_region_profile p ON p.region_cd = a.region_cd
                    WHERE YEAR(a.target_date) = (SELECT MAX(YEAR(target_date))
                                                 FROM ref_actual_daily)
                    GROUP BY a.region_cd, p.population""")
    h = pd.DataFrame(rows)
    if len(h):
        df = df.merge(h, on="region_cd", how="left")
    else:
        df["전년화재율"] = np.nan

    rows2 = query("SELECT region_cd, assign_method FROM ref_station_map")
    s = pd.DataFrame(rows2)
    if len(s):
        df = df.merge(s.rename(columns={"assign_method": "기상배정방식"}),
                      on="region_cd", how="left")
    else:
        df["기상배정방식"] = None
    return df
