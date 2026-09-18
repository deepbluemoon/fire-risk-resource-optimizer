"""T025 — 지역 군집. 251행, 라벨 고유, 화재 실적 미사용(누수 차단)."""
from __future__ import annotations

import pytest

from fire_ml.data.regions import region_master
from fire_ml.features.region import CLUSTER_INPUTS, K, fit_clusters, label_clusters


@pytest.fixture(scope="module")
def master():
    return region_master()


def test_master_is_251_rows(master):
    assert len(master) == 251
    assert master["region_cd"].is_unique


def test_cluster_inputs_contain_no_fire_history():
    """군집 입력에 화재 실적을 넣으면 즉시 누수다."""
    for c in CLUSTER_INPUTS:
        assert "화재" not in c and "전년" not in c, f"군집 입력에 실적 파생 {c}"


def test_clusters_cover_all_regions(master):
    c = fit_clusters(master)
    assert len(c) == 251
    assert c["cluster_id"].nunique() == K


def test_labels_are_unique(master):
    lab = label_clusters(fit_clusters(master), master)
    assert lab["cluster_label"].nunique() == K, "군집마다 서로 다른 이름이어야 한다"


def test_clusters_separate_fire_rate(master):
    """군집이 발생률을 실제로 가른다 — plan.md §M-3 은 4.23배 분리를 보고했다."""
    from fire_ml.data.loaders import load_dev
    from fire_ml.data.regions import region_cd

    dev = load_dev("occurrence")
    dev["region_cd"] = region_cd(dev["시도"], dev["시군구"])
    lab = label_clusters(fit_clusters(master), master)
    j = dev.merge(lab[["region_cd", "cluster_id"]], on="region_cd")
    rate = j.groupby("cluster_id")["화재건수"].mean()
    assert rate.max() / rate.min() > 3.0, f"군집간 분리가 약하다: {rate.max()/rate.min():.2f}배"
