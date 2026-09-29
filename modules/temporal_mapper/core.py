"""Minimal Python port of GraphExplain's active TMapper_R pipeline.

See README for source attribution, deliberate scope, and binning semantics.
"""

import json
from collections import Counter, defaultdict
from itertools import pairwise

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.sparse.csgraph import connected_components, shortest_path
from scipy.spatial.distance import squareform


def period_of(timestamp, unit):
    t = pd.Timestamp(timestamp).tz_convert("UTC").tz_localize(None)
    freq = {"day": "D", "week": "W-SUN", "month": "M", "year": "Y"}[unit]
    return t.to_period(freq).start_time


def build_patterns(events, time_unit="day"):
    """Preserve R's case-start activity / first-transition edge binning."""
    if time_unit not in {"day", "week", "month", "year"}:
        raise ValueError("Choose day, week, month, or year.")
    required = ["case_id", "activity", "timestamp"]
    if not set(required).issubset(events.columns):
        raise ValueError("The log needs case_id, activity, and timestamp.")
    if len(events) > 250_000:
        raise ValueError(
            "This initial module supports at most 250,000 events. Filter the log first."
        )
    df = events[required].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce", format="mixed")
    valid = df.notna().all(axis=1) & df["activity"].astype(str).str.strip().ne("")
    dropped = int((~valid).sum())
    df = df.loc[valid].sort_values("timestamp", kind="stable")
    if df.empty:
        raise ValueError("No events with valid case, activity, and timestamp remain.")
    rows = defaultdict(Counter)
    for _, case in df.groupby("case_id", sort=False):
        actions = case["activity"].astype(str).tolist()
        times = case["timestamp"].tolist()
        start = period_of(times[0], time_unit)
        rows[start].update({"activity:" + a: count for a, count in Counter(actions).items()})
        transitions = [(i, a, b) for i, (a, b) in enumerate(pairwise(actions)) if a != b]
        if transitions:
            edge_period = period_of(times[transitions[0][0]], time_unit)
            # JSON tuples avoid collisions when activity labels contain hyphens.
            rows[edge_period].update(
                "transition:" + json.dumps([a, b], ensure_ascii=False) for _, a, b in transitions
            )
    if len(rows) > 800:
        raise ValueError(
            "More than 800 occupied periods. Choose a coarser time unit or filter the log."
        )
    features = sorted({key for row in rows.values() for key in row})
    if len(features) > 5000:
        raise ValueError("More than 5,000 pattern features. Filter the activity set first.")
    periods = sorted(rows)
    matrix = np.array([[rows[p][f] for f in features] for p in periods], dtype=float)
    return matrix, periods, features, dropped


def cosine_similarity(matrix):
    x = np.asarray(matrix, dtype=float)
    if x.ndim != 2 or not len(x) or not x.shape[1] or not np.isfinite(x).all():
        raise ValueError("Patterns must be a nonempty finite numeric matrix.")
    norms = np.linalg.norm(x, axis=1)
    zero = norms == 0
    safe = np.where(zero, 1, norms)
    similarity = (x / safe[:, None]) @ (x / safe[:, None]).T
    similarity[np.ix_(zero, zero)] = 1
    np.fill_diagonal(similarity, 1)
    return np.clip(similarity, -1, 1)


def normalize_patterns(matrix, features):
    """L2-normalize activity and transition blocks separately within each period."""
    normalized = np.asarray(matrix, dtype=float).copy()
    for prefix in ("activity:", "transition:"):
        columns = [i for i, feature in enumerate(features) if feature.startswith(prefix)]
        block = normalized[:, columns]
        norms = np.linalg.norm(block, axis=1, keepdims=True)
        normalized[:, columns] = block / np.where(norms == 0, 1, norms)
    return normalized


def reduce_patterns(matrix, variance_percent=95):
    """Centered, unscaled PCA; retain at least one component for cosine similarity."""
    if not np.isfinite(variance_percent) or not 0 <= variance_percent <= 100:
        raise ValueError("PCA retained variance must be between 0 and 100 percent.")
    x = np.asarray(matrix, dtype=float)
    centered = x - x.mean(axis=0)
    u, singular, _ = np.linalg.svd(centered, full_matrices=False)
    tolerance = np.finfo(float).eps * max(centered.shape) * singular[0]
    rank = int(np.count_nonzero(singular > tolerance))
    if rank == 0:
        return np.zeros((len(x), 1)), {
            "components": 0,
            "retained_variance_percent": 0.0,
            "note": "PCA has no variance to retain; all periods have identical patterns.",
        }
    cumulative = np.cumsum(singular[:rank] ** 2)
    cumulative /= cumulative[-1]
    count = min(rank, max(1, int(np.searchsorted(cumulative, variance_percent / 100)) + 1))
    scores = u[:, :count] * singular[:count]
    # Prevent roundoff at the mean from creating arbitrary cosine directions.
    scores[np.linalg.norm(scores, axis=1) <= tolerance] = 0
    return scores, {
        "components": count,
        "retained_variance_percent": float(cumulative[count - 1] * 100),
        "note": None,
    }


def map_patterns(
    matrix,
    periods,
    similarity_threshold=0.85,
    method="hclust",
    temporal_edges="consecutive",
    time_unit="day",
    loop_size=2,
):
    if not 0 <= similarity_threshold <= 1 or not 1 <= loop_size <= 20:
        raise ValueError("Similarity must be between 0 and 1; loop size must be between 1 and 20.")
    if method not in {"hclust", "heuristic"} or temporal_edges not in {"consecutive", "adjacent"}:
        raise ValueError("Unknown clustering or temporal-edge mode.")
    similarity = cosine_similarity(matrix)
    n = len(similarity)
    if len(periods) != n or n > 800:
        raise ValueError("Expected one date per pattern, with at most 800 patterns.")
    if method == "hclust" and n > 1:
        distance = np.maximum(0, 1 - similarity)
        distance = (distance + distance.T) / 2
        np.fill_diagonal(distance, 0)
        tree = linkage(squareform(distance), method="average")
        clusters = fcluster(
            tree, max(1 - similarity_threshold, np.finfo(float).eps), criterion="distance"
        )
        adjacency = clusters[:, None] == clusters[None, :]
    else:
        adjacency = similarity >= similarity_threshold
        if method == "heuristic":
            indices = np.arange(n - 1)
            adjacency[indices, indices + 1] = False
            adjacency[indices + 1, indices] = False
    gaps = []
    for i in range(n - 1):
        a, b = pd.Timestamp(periods[i]), pd.Timestamp(periods[i + 1])
        delta = {
            "day": (b - a).days,
            "week": (b - a).days / 7,
            "month": (b.year - a.year) * 12 + b.month - a.month,
            "year": b.year - a.year,
        }[time_unit]
        gaps.append(max(delta - 1, 0))
        if temporal_edges == "consecutive" or gaps[-1] == 0:
            adjacency[i, i + 1] = True
    np.fill_diagonal(adjacency, False)
    distances = shortest_path(adjacency.astype(float), directed=True, unweighted=True)
    mutual = (distances < loop_size) & (loop_size > distances.T)
    np.fill_diagonal(mutual, False)
    count, labels = connected_components(mutual, directed=False)
    simplified = np.zeros((count, count), dtype=bool)
    source, target = np.nonzero(adjacency)
    simplified[labels[source], labels[target]] = True
    members = [np.flatnonzero(labels == i).tolist() for i in range(count)]
    return {
        "members": members,
        "adjacency": adjacency.astype(int).tolist(),
        "simplified": simplified.astype(int).tolist(),
        "gaps": gaps,
        "similarity": similarity.tolist(),
    }


def compute(
    events,
    time_unit="day",
    similarity_threshold=0.85,
    method="hclust",
    temporal_edges="consecutive",
    loop_size=2,
    pca_enabled=False,
    pca_variance=95,
):
    matrix, periods, features, dropped = build_patterns(events, time_unit)
    normalized = normalize_patterns(matrix, features)
    mapped_matrix, pca = (
        reduce_patterns(normalized, pca_variance) if pca_enabled else (normalized, None)
    )
    result = map_patterns(
        mapped_matrix, periods, similarity_threshold, method, temporal_edges, time_unit, loop_size
    )
    nodes = []
    for i, members in enumerate(result["members"]):
        totals = matrix[members].sum(axis=0)
        top = sorted(range(len(features)), key=lambda j: (-totals[j], features[j]))[:8]
        nodes.append(
            {
                "id": str(i + 1),
                "size": len(members),
                "members": [periods[j].date().isoformat() for j in members],
                "time": float(np.mean(members)),
                "patterns": [
                    {"name": features[j], "count": int(totals[j])} for j in top if totals[j] > 0
                ],
            }
        )
    edges = [
        {"source": str(i + 1), "target": str(j + 1)}
        for i, row in enumerate(result["simplified"])
        for j, value in enumerate(row)
        if value and i != j
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "periods": [p.date().isoformat() for p in periods],
        "n_features": len(features),
        "pca": pca,
        "n_gaps": sum(g > 0 for g in result["gaps"]),
        "dropped_events": dropped,
        "parameters": {
            "time_unit": time_unit,
            "similarity_threshold": similarity_threshold,
            "method": method,
            "temporal_edges": temporal_edges,
            "loop_size": loop_size,
            "normalization": "per_period_separate_l2",
            "pca_enabled": pca_enabled,
            "pca_variance": pca_variance,
        },
    }
