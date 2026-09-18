"""T024 — 지역 군집 (k=6). 정적 프로파일만 사용한다.

군집 입력에 화재 실적을 넣으면 즉시 누수다. 적합은 개발셋 구간에서만.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

CLUSTER_INPUTS = ["log_인구", "인구밀도", "산림율", "log_면적", "평균소방서거리"]
K = 6
SEED = 20260901


def fit_clusters(master: pd.DataFrame) -> pd.DataFrame:
    m = master.copy()
    m["log_면적"] = np.log1p(m["면적_km2"].astype(float))
    X = m[CLUSTER_INPUTS].astype(float)
    X = X.fillna(X.median())
    Z = StandardScaler().fit_transform(X)
    km = KMeans(n_clusters=K, random_state=SEED, n_init=20).fit(Z)
    m["cluster_id"] = km.labels_.astype(int)
    return m[["region_cd", "sido", "sigungu", "cluster_id"]]


def label_clusters(clusters: pd.DataFrame, master: pd.DataFrame,
                   rates: pd.Series | None = None) -> pd.DataFrame:
    """군집에 사람이 읽을 이름을 붙인다 (밀도·산림율·소방서거리 기준)."""
    j = clusters.merge(master, on=["region_cd", "sido", "sigungu"])
    prof = j.groupby("cluster_id").agg(
        density=("인구밀도", "median"), forest=("산림율", "median"),
        dist=("평균소방서거리", "median"), n=("region_cd", "size"))
    # 각 군집에 서로 다른 이름을 준다 (탐욕적 배정, 순서 고정)
    remaining = set(prof.index)
    names: dict[int, str] = {}

    def take(col: str, label: str, ascending: bool = False) -> None:
        if not remaining:
            return
        cid = prof.loc[sorted(remaining)][col].sort_values(ascending=ascending).index[0]
        names[cid] = label
        remaining.discard(cid)

    take("dist", "산간 오지")                 # 소방서 최원거리
    take("density", "도심 고밀")              # 최고밀
    take("forest", "농산촌")                  # 최고 산림율
    take("density", "수도권 위성도시")          # 남은 것 중 최고밀
    take("n", "도농복합 중견도시")              # 남은 것 중 최다 지역수
    for cid in sorted(remaining):
        names[cid] = "소도시"

    out = clusters.copy()
    out["cluster_label"] = out["cluster_id"].map(names)
    return out
