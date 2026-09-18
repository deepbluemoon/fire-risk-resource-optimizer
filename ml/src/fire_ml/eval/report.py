"""T043 — 합격 기준 판정 (plan.md §M-9).

홀드아웃 2023 을 여기서 처음 개봉한다. 하이퍼파라미터를 보고 다시 돌리면 홀드아웃이 아니게 된다.
미통과 모델은 적재하지 않는다.
"""
from __future__ import annotations

from typing import Any

# (모델, 기준명, 판정함수, 기준 설명)
CRITERIA: dict[str, list[tuple[str, Any, str]]] = {
    "M1": [
        ("홀드아웃 deviance < 0.9080", lambda m: m["poisson_deviance"] < 0.9080,
         "M0-지역×계절 0.9108 을 유의하게 개선"),
        ("캘리브레이션 0.95~1.05", lambda m: 0.95 <= m["calibration"] <= 1.05,
         "배분이 기대 건수에 비례하므로 순위보다 선행 조건 (SC-011)"),
        ("recall@20% > 0.376", lambda m: m["recall@20%"] > 0.376,
         "지역 oracle 상한 37.6% 초과 = 날씨 축의 실재 증명"),
    ],
    "M5": [
        ("Brier 가 M0 유도 대비 개선", lambda m: m["brier"] < m["brier_baseline"],
         "확률을 숫자로 노출하므로 정확도가 곧 사용자 오해 여부"),
        ("10분위 캘리브레이션 편차 < 5%p", lambda m: m["calib_max_dev_pp"] < 5.0,
         "40% 로 표시된 날의 실제 발생률이 40% 여야 한다"),
    ],
    "M2a": [
        ("PR-AUC > 기저율 × 1.5", lambda m: m["pr_auc"] > m["base_rate"] * 1.5,
         "일 단위 재정의 후 기저율 재산정 기준"),
    ],
    "M3": [
        ("top-3 > 0.917", lambda m: m["top3"] > 0.917,
         "최빈3 고정 제시 상수 베이스라인 91.70% 초과"),
        ("소수클래스 lift > 2.0", lambda m: m["minority_lift"] > 2.0,
         "부주의를 또 맞히는 것이 아니라 이상 조건을 알리는 것이 가치"),
    ],
    "M4": [
        ("공식 대형화재 상위 10% 포착 > 0.50", lambda m: m["official_recall_at_10"] > 0.50,
         "외부 검증 — 가장 신뢰할 수 있는 기준"),
    ],
}


def judge(model_id: str, metrics: dict) -> dict:
    rows = []
    for name, fn, why in CRITERIA.get(model_id, []):
        try:
            ok = bool(fn(metrics))
        except (KeyError, TypeError):
            ok = False
            why += " (지표 없음)"
        rows.append({"기준": name, "판정": "PASS" if ok else "FAIL", "근거": why})
    passed = bool(rows) and all(r["판정"] == "PASS" for r in rows)
    return {"model_id": model_id, "criteria": rows, "acceptance_passed": passed,
            "metrics": metrics}


def render(results: list[dict]) -> str:
    out = ["| 모델 | 기준 | 판정 | 실측 |", "|---|---|---|---|"]
    for r in results:
        m = r["metrics"]
        for c in r["criteria"]:
            out.append(f"| {r['model_id']} | {c['기준']} | **{c['판정']}** | "
                       f"{_pick(c['기준'], m)} |")
    return "\n".join(out)


def _pick(criterion: str, m: dict) -> str:
    for k in ("poisson_deviance", "calibration", "recall@20%", "brier",
              "calib_max_dev_pp", "pr_auc", "top3", "minority_lift",
              "official_recall_at_10"):
        if k.split("_")[0] in criterion or k in criterion:
            if k in m:
                return f"{m[k]:.4f}"
    return "—"
