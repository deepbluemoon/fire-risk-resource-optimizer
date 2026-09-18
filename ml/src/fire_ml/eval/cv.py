"""Expanding-window TimeSeriesSplit (plan.md §M-8).

무작위 KFold·StratifiedKFold 는 사용 금지다.
시군구×일 패널이라 같은 지역의 인접일이 train/val 로 갈라지고,
이웃한 날의 기상은 거의 동일해 성능이 크게 부풀려진다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# plan.md §M-8 — 다섯 폴드가 사계절을 모두 덮는다
FOLDS: list[tuple[tuple[str, str], tuple[str, str]]] = [
    (("2021-01-01", "2021-09-30"), ("2021-10-01", "2021-12-31")),
    (("2021-01-01", "2021-12-31"), ("2022-01-01", "2022-03-31")),
    (("2021-01-01", "2022-03-31"), ("2022-04-01", "2022-06-30")),
    (("2021-01-01", "2022-06-30"), ("2022-07-01", "2022-09-30")),
    (("2021-01-01", "2022-09-30"), ("2022-10-01", "2022-12-31")),
]

# 과적합 판정 — 절대 격차가 아니라 **구조적 격차 대비 초과분**으로 본다.
#
# plan.md §4-2 의 +0.02 기준은 *동일 분할*(train 2021-01~2022-06 / test 2023)에서
# B1(+0.0043) 과 B2(+0.0347) 사이로 잡은 값이다. Expanding-window CV 에서는
# 폴드마다 검증 구간의 계절이 달라 격차가 저절로 커진다 — 실측으로,
# 과적합이 구조적으로 불가능한 M0(255 파라미터 룩업)조차 CV 격차가 +0.0281 이다.
# 따라서 CV 에서는 같은 폴드를 통과한 M0 의 격차를 기준선으로 두고 초과분만 본다.
OVERFIT_GAP_THRESHOLD = 0.02          # 동일 분할 판정용 (plan.md §4-2)
STRUCTURAL_GAP_M0 = 0.0281            # 5폴드 CV 에서 M0 가 내는 격차 (실측 2026-09-01)
OVERFIT_EXCESS_THRESHOLD = 0.015      # 구조적 격차 대비 초과 허용치


def is_overfit(gap: float, structural: float = STRUCTURAL_GAP_M0) -> bool:
    """CV 격차가 구조적 기준선을 유의하게 넘는가."""
    return (gap - structural) > OVERFIT_EXCESS_THRESHOLD


def expanding_splits(dates: pd.Series) -> list[tuple[np.ndarray, np.ndarray]]:
    """날짜 시리즈에서 5폴드 (train_idx, valid_idx) 를 만든다."""
    d = pd.to_datetime(pd.Series(dates).reset_index(drop=True))
    out = []
    for (ts, te), (vs, ve) in FOLDS:
        tr = np.where((d >= ts) & (d <= te))[0]
        va = np.where((d >= vs) & (d <= ve))[0]
        if len(tr) and len(va):
            out.append((tr, va))
    return out


def _forbidden(name: str):
    raise NotImplementedError(
        f"{name} 은(는) 이 프로젝트에서 사용 금지다. 시군구×일 패널에서 시간 누수와 "
        f"인접일 누수를 일으켜 성능을 부풀린다. expanding_splits() 를 사용하라. (plan.md §M-8)"
    )


def KFold(*_a, **_k):            # noqa: N802 — 오용 차단용 동명 함수
    _forbidden("무작위 KFold")


def StratifiedKFold(*_a, **_k):  # noqa: N802
    _forbidden("StratifiedKFold")
