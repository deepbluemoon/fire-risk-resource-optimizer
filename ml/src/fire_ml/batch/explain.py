"""기여 요인 문장 생성 (FR-015 · FR-021 · FR-034).

"제1주성분이 낮습니다"는 문장이 될 수 없다. 비전문가가 읽을 수 있어야 한다.
근거 없는 수사를 붙이지 않고, 실제 모델이 쓴 값과 배율만 문장으로 옮긴다.
"""
from __future__ import annotations

SEASON_KO = {"봄": "봄철", "여름": "여름철", "가을": "가을철", "겨울": "겨울철"}


def region_factor(ratio: float, cluster_label: str | None) -> dict:
    direction = "상승" if ratio >= 1.0 else "하락"
    kind = f"{cluster_label} 유형으로 " if cluster_label else ""
    return {
        "feature": "이 지역의 평소 위험",
        "value": round(ratio, 3),
        "direction": direction,
        "magnitude": round(abs(ratio - 1.0), 3),
        "sentenceKo": (f"이 지역은 {kind}평소에도 화재가 전국 평균보다 {ratio:.1f}배 많습니다."
                       if ratio >= 1.0 else
                       f"이 지역은 {kind}평소 화재가 전국 평균보다 적은 편입니다."),
    }


def season_factor(season: str, factor: float) -> dict:
    return {
        "feature": "계절",
        "value": round(factor, 3),
        "direction": "상승" if factor >= 1.0 else "하락",
        "magnitude": round(abs(factor - 1.0), 3),
        "sentenceKo": (f"{SEASON_KO.get(season, season)}은 1년 중 화재가 {factor:.1f}배 많은 시기입니다." if factor >= 1.0 else
                       f"{SEASON_KO.get(season, season)}은 1년 중 화재가 적은 시기입니다."),
    }


def confidence_factor(assign_method: str) -> dict | None:
    """대리 관측지점을 쓴 경우 그 사실을 드러낸다 (spec.md Edge Cases)."""
    if assign_method in (None, "관내"):
        return None
    return {
        "feature": "날씨 관측소",
        "value": None,
        "direction": "하락",
        "magnitude": 0.0,
        "sentenceKo": ("이 지역에는 기상 관측소가 없어 가까운 다른 지역의 날씨를 대신 썼습니다. "
                       "그만큼 예측이 덜 정확할 수 있습니다."),
    }


def weather_unavailable_factor() -> dict:
    return {
        "feature": "오늘 날씨",
        "value": None,
        "direction": "하락",
        "magnitude": 0.0,
        "sentenceKo": ("오늘 날씨 정보를 받지 못해 이 지역의 평소 수준으로만 계산했습니다. "
                       "날씨에 따른 변화는 반영되지 않았습니다."),
    }


def build_factors(*parts, limit: int = 3) -> list[dict]:
    """상위 요인을 고른다. 크기순으로 자르면 안 된다.

    지역 기저 배율은 4배까지 나오고 날씨 기여는 0.02~0.4배 수준이라, 크기순 정렬만 하면
    **날씨도 확산도 절대 상위에 못 든다.** 그런데 "오늘 왜 위험한가"에서 지역은 매일 같은 값이다.
    종류별로 한 칸씩 먼저 배분한 뒤 남은 칸을 크기순으로 채운다.
    """
    fs = [p for p in parts if p]
    order = ["weather", "spread", None]      # 우선 배분 순서 (None = 그 외)
    buckets: dict = {}
    for f in fs:
        buckets.setdefault(f.get("kind"), []).append(f)
    for v in buckets.values():
        v.sort(key=lambda d: d.get("magnitude") or 0.0, reverse=True)

    picked: list[dict] = []
    for kind in order:                        # 1) 종류마다 한 칸
        if len(picked) >= limit:
            break
        pool = buckets.get(kind) or []
        if pool:
            picked.append(pool.pop(0))

    rest = [f for v in buckets.values() for f in v]   # 2) 남은 칸은 크기순
    rest.sort(key=lambda d: d.get("magnitude") or 0.0, reverse=True)
    for f in rest:
        if len(picked) >= limit:
            break
        picked.append(f)
    return picked[:limit]


# --- 날씨 기여 요인 (M1-GLM 계수 기반) ---------------------------------------
#
# GLM 을 보조로 함께 학습해 두는 이유가 여기다 — 계수가 곧 설명이다.
# 트리(M1-GBM)가 예측을 내고, 같은 데이터로 적합한 GLM 이 "왜"를 문장으로 만든다.
# 두 모델의 순위가 다를 수 있으므로 **크기가 아니라 방향과 조건만** 문장에 쓴다.

# 항 묶음 — GLM 이 하나의 물리량을 1·2차항으로 쪼개 갖고 있으므로 합산해서 설명한다.
# 쪼갠 채로 상위 항만 고르면 가장 큰 동인(건조도)이 통째로 숨는다.
_GROUPS: dict[str, dict] = {
    "건조": {
        "terms": ["건조도", "건조도2"],
        "label": "공기 건조도",
        "value": lambda d, i: 100 - float(d["건조도"].iloc[i]) * 100,
        "sentence": lambda v: (
            f"공기가 메마른 정도가 {v:.0f}% 수준으로 "
            + ("매우 건조합니다." if v < 35 else "건조합니다." if v < 50 else
               "보통 수준입니다." if v < 70 else "습한 편입니다.")),
    },
    "최소습도": {
        "terms": ["최소습도결핍"], "label": "낮 최저 습도",
        "value": lambda d, i: 100 - float(d["최소습도결핍"].iloc[i]) * 100,
        "sentence": lambda v: f"낮에 습도가 {v:.0f}%까지 떨어집니다.",
    },
    "무강수": {
        "terms": ["무강수sqrt", "건조지속강도"], "label": "비 안 온 기간",
        "value": lambda d, i: float(d["무강수sqrt"].iloc[i]) ** 2,
        "sentence": lambda v: f"비가 안 온 지 {v:.0f}일째입니다.",
    },
    "일교차": {
        "terms": ["일교차"], "label": "하루 기온차",
        "value": lambda d, i: float(d["일교차"].iloc[i]) * 10,
        "sentence": lambda v: f"낮과 밤의 기온차가 {v:.0f}도입니다.",
    },
    "바람": {
        "terms": ["풍속", "풍속건조지수"], "label": "바람",
        "value": lambda d, i: float(d["풍속"].iloc[i]),
        "sentence": lambda v: f"바람이 초속 {v:.1f}m로 붑니다.",
    },
    "한랭건조": {
        "terms": ["한랭건조"], "label": "춥고 건조함",
        "value": lambda d, i: float(d["한랭건조"].iloc[i]),
        "sentence": lambda v: "춥고 건조한 날씨입니다.",
    },
    "강수": {
        "terms": ["강수있음"], "label": "비",
        "value": lambda d, i: float(d["강수있음"].iloc[i]),
        "sentence": lambda v: ("오늘 비가 와서 위험이 낮아집니다." if v > 0.5
                               else "오늘 비 예보가 없습니다."),
    },
}


def weather_factors_from_glm(X, coefs: dict, top: int = 2) -> list[list[dict]]:
    """행별 날씨 기여 요인.

    묶음별 로그 기여 = Σ 계수 × (값 − 중앙값). 이를 배율로 환산해 문장으로 만든다.
    M1-GBM 이 예측을 내고, 같은 데이터로 적합한 M1-GLM 이 '왜'를 설명한다 —
    두 모델의 순위가 다를 수 있으므로 문장에는 **방향과 조건**만 쓰고 예측값은 쓰지 않는다.
    """
    import numpy as np

    from ..models.m1_count import _glm_design

    D = _glm_design(X)
    ref = D.median(numeric_only=True)

    names, mats = [], []
    for gname, g in _GROUPS.items():
        terms = [t for t in g["terms"] if t in D.columns and float(coefs.get(t, 0.0)) != 0.0]
        if not terms:
            continue
        contrib = sum(float(coefs[t]) * (D[t].to_numpy(float) - float(ref[t])) for t in terms)
        names.append(gname)
        mats.append(contrib)

    if not names:
        return [[] for _ in range(len(D))]

    M = np.vstack(mats)                              # (groups, rows)
    order = np.argsort(-np.abs(M), axis=0)[:top]
    out: list[list[dict]] = []
    for i in range(M.shape[1]):
        fs = []
        for j in order[:, i]:
            g = _GROUPS[names[j]]
            val = g["value"](D, i)
            mult = float(np.exp(M[j, i]))
            fs.append({
                "kind": "weather",
                "feature": g["label"],
                "value": round(val, 2),
                "direction": "상승" if M[j, i] >= 0 else "하락",
                "magnitude": round(abs(mult - 1.0), 3),
                "sentenceKo": f"{g['sentence'](val)} 평소보다 위험이 {mult:.1f}배입니다.",
            })
        out.append(fs)
    return out


def spread_factor(prob: float | None, station_km: float | None,
                  forest_ratio: float | None) -> dict | None:
    """확산 기여 요인 (US2 시나리오 2 · FR-034).

    실측: 소방서거리 10km 이상 지역의 연소확대율 43.3% — 2km 미만(18.1%)의 2.4배.
    자산가치와 무관한 지표이므로 도심 편향이 없다 (FR-032).
    """
    import math

    if prob is None or (isinstance(prob, float) and math.isnan(prob)):
        return None
    parts = []
    if station_km is not None and not (isinstance(station_km, float) and math.isnan(station_km)):
        parts.append(f"소방서까지 평균 {station_km:.0f}km")
    if forest_ratio is not None and not (isinstance(forest_ratio, float) and math.isnan(forest_ratio)):
        parts.append(f"산림 비중 {forest_ratio:.0f}%")
    why = (" · ".join(parts)) if parts else "지역·기상 조건"
    return {
        "kind": "spread",
        "feature": "불이 번질 가능성",
        "value": round(float(prob), 3),
        "direction": "상승" if prob >= 0.297 else "하락",
        "magnitude": round(abs(float(prob) - 0.297), 3),
        "sentenceKo": (f"이곳에서 불이 나면 번질 가능성이 {prob * 100:.0f}%입니다 "
                       f"(전국 평균 30%). {why}."),
    }
