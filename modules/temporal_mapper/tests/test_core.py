import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from modules.temporal_mapper.core import build_patterns, compute, map_patterns

REFERENCE = json.loads(Path(__file__).with_name("r_reference.json").read_text())


@pytest.mark.parametrize("case", REFERENCE["cases"])
def test_matches_original_r_pipeline(case):
    result = map_patterns(
        REFERENCE["matrix"],
        REFERENCE["periods"],
        time_unit="week",
        method=case["method"],
        temporal_edges=case["mode"],
        loop_size=case["d"],
    )
    np.testing.assert_allclose(result["similarity"], case["similarity"], atol=1e-14)
    assert result["adjacency"] == case["adjacency"]
    assert result["simplified"] == case["simplified"]
    expected = (
        list(case["members"].values()) if isinstance(case["members"], dict) else case["members"]
    )
    assert result["members"] == [m if isinstance(m, list) else [m] for m in expected]


def test_case_boundaries_stable_ties_and_r_period_assignment():
    events = pd.DataFrame(
        [
            ["a", "A", "2024-01-01"],
            ["b", "X", "2024-01-01"],
            ["a", "A", "2024-01-08"],
            ["a", "B", "2024-01-08"],
            ["b", "Y", "2024-01-02"],
        ],
        columns=["case_id", "activity", "timestamp"],
    )
    matrix, periods, features, dropped = build_patterns(events, time_unit="week")
    assert dropped == 0
    assert [p.date().isoformat() for p in periods] == ["2024-01-01", "2024-01-08"]
    assert matrix[0, features.index("activity:A")] == 2
    assert matrix[1, features.index('transition:["A", "B"]')] == 1
    assert not any('"A", "X"' in f for f in features)


def test_single_period_keeps_isolated_node():
    events = pd.DataFrame({"case_id": ["a"], "activity": ["A"], "timestamp": ["2024-01-01"]})
    result = compute(events)
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["members"] == ["2024-01-01"]
    assert result["edges"] == []


def test_daily_default_handles_mixed_timestamp_formats_and_offsets():
    events = pd.DataFrame(
        {
            "case_id": ["a", "b", "c", "bad"],
            "activity": ["A", "B", "C", "D"],
            "timestamp": [
                "2024-01-01",
                "2024-01-02T12:00:00Z",
                "2024-01-02T23:30:00-05:00",
                "invalid",
            ],
        }
    )
    result = compute(events)
    assert result["parameters"]["time_unit"] == "day"
    assert result["periods"] == ["2024-01-01", "2024-01-02", "2024-01-03"]
    assert result["dropped_events"] == 1


def test_invalid_input_and_period_limit():
    with pytest.raises(ValueError, match="finite"):
        map_patterns([[float("nan")]], ["2024-01-01"])
    with pytest.raises(ValueError, match="800"):
        build_patterns(
            pd.DataFrame(
                {
                    "case_id": range(801),
                    "activity": ["A"] * 801,
                    "timestamp": pd.date_range("2020-01-01", periods=801),
                }
            ),
            "day",
        )


def test_pca_targets_and_full_variance_geometry():
    from modules.temporal_mapper.core import cosine_similarity, reduce_patterns

    matrix = np.array([[3, 0], [-3, 0], [0, 1], [0, -1]], dtype=float) + 5
    for target, expected in [(0, 1), (90, 1), (95, 2), (100, 2)]:
        scores, info = reduce_patterns(matrix, target)
        assert info["components"] == expected
        assert info["retained_variance_percent"] >= target
    np.testing.assert_allclose(
        cosine_similarity(scores), cosine_similarity(matrix - matrix.mean(axis=0)), atol=1e-14
    )
    for target in [-1, 101, float("nan")]:
        with pytest.raises(ValueError, match="variance"):
            reduce_patterns(matrix, target)


@pytest.mark.parametrize("matrix", [[[2, 3]], [[2, 3], [2, 3]]])
def test_pca_constant_patterns(matrix):
    from modules.temporal_mapper.core import reduce_patterns

    scores, info = reduce_patterns(matrix)
    assert np.isfinite(scores).all()
    assert not scores.any()
    assert info["components"] == 0
    assert info["note"]


def test_pca_integration_preserves_counts_and_default():
    events = pd.DataFrame(
        {
            "case_id": ["a", "b", "c"],
            "activity": ["A", "B", "A"],
            "timestamp": ["2024-01-01", "2024-01-02", "2024-01-03"],
        }
    )
    assert compute(events) == compute(events, pca_enabled=False)
    result = compute(events, pca_enabled=True, pca_variance=95, loop_size=1)
    assert result["pca"]["components"] == 1
    assert result["pca"]["retained_variance_percent"] == pytest.approx(100)
    assert sum(p["count"] for n in result["nodes"] for p in n["patterns"]) == 3


def test_separate_normalization_balances_blocks_and_preserves_zeros():
    from modules.temporal_mapper.core import normalize_patterns

    features = ["activity:A", "activity:B", "transition:AB", "transition:BA"]
    matrix = np.array([[3.0, 4.0, 0.0, 20.0], [0.0, 0.0, 0.0, 0.0], [6.0, 8.0, 0.0, 2.0]])
    original = matrix.copy()
    actual = normalize_patterns(matrix, features)
    np.testing.assert_allclose(actual, [[0.6, 0.8, 0, 1], [0, 0, 0, 0], [0.6, 0.8, 0, 1]])
    np.testing.assert_array_equal(matrix, original)
    np.testing.assert_allclose(normalize_patterns([[5]], ["activity:A"]), [[1]])


@pytest.mark.parametrize("pca_enabled", [False, True])
def test_compute_normalizes_before_pca_and_mapping(monkeypatch, pca_enabled):
    from modules.temporal_mapper import core

    events = pd.DataFrame(
        {
            "case_id": ["a", "a", "a", "b", "b"],
            "activity": ["A", "A", "B", "B", "A"],
            "timestamp": ["2024-01-01"] * 3 + ["2024-01-02"] * 2,
        }
    )
    raw, _, features, _ = core.build_patterns(events)
    expected = core.normalize_patterns(raw, features)
    original_reduce, original_map = core.reduce_patterns, core.map_patterns
    seen = []

    def reduce(matrix, variance):
        np.testing.assert_allclose(matrix, expected)
        seen.append("pca")
        return original_reduce(matrix, variance)

    def map_matrix(matrix, *args):
        target = original_reduce(expected, 95)[0] if pca_enabled else expected
        np.testing.assert_allclose(matrix, target)
        seen.append("map")
        return original_map(matrix, *args)

    monkeypatch.setattr(core, "reduce_patterns", reduce)
    monkeypatch.setattr(core, "map_patterns", map_matrix)
    result = core.compute(events, pca_enabled=pca_enabled, loop_size=1)
    assert seen == (["pca", "map"] if pca_enabled else ["map"])
    assert sum(p["count"] for n in result["nodes"] for p in n["patterns"]) == 7
