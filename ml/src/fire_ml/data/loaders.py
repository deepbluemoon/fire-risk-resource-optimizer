"""데이터셋 로드 및 개발셋/홀드아웃 재조립 (plan.md §D-2).

기존 train/val/test 3분할은 val 이 7~12월만 덮어 계절 편향이 있다.
개발셋(2021-01~2022-12)으로 합쳐 CV 하고, 홀드아웃(2023)은 최종 1회만 개봉한다.
"""
from __future__ import annotations

import pandas as pd

from ..config import DATA_DIR, DEV_END, DEV_START, HOLDOUT_END, HOLDOUT_START

_KINDS = ("occurrence", "spread", "cause")


def _read(kind: str, split: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / f"{kind}_{split}.csv")
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df


def load_raw(kind: str) -> pd.DataFrame:
    """train+val+test 를 모두 이어붙인 원본."""
    if kind not in _KINDS:
        raise ValueError(f"kind 는 {_KINDS} 중 하나")
    return pd.concat([_read(kind, s) for s in ("train", "val", "test")], ignore_index=True)


def load_dev(kind: str = "occurrence") -> pd.DataFrame:
    """개발셋 2021-01-01 ~ 2022-12-31. 하이퍼파라미터·early stopping 전부 여기서."""
    df = load_raw(kind)
    return df[(df["날짜"] >= DEV_START) & (df["날짜"] <= DEV_END)].reset_index(drop=True)


def load_holdout(kind: str = "occurrence", *, unseal: bool = False) -> pd.DataFrame:
    """홀드아웃 2023. 최종 1회 판정 전용.

    실수 개봉을 막기 위해 unseal=True 를 명시해야 한다.
    """
    if not unseal:
        raise PermissionError(
            "홀드아웃(2023)은 최종 1회만 개봉한다. 하이퍼파라미터를 보고 다시 돌리면 "
            "홀드아웃이 아니게 된다. 정말 판정 단계라면 unseal=True 를 명시하라."
        )
    df = load_raw(kind)
    return df[(df["날짜"] >= HOLDOUT_START) & (df["날짜"] <= HOLDOUT_END)].reset_index(drop=True)
