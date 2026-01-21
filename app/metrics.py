from __future__ import annotations

from prometheus_client import Counter, Gauge

# Node-level metrics
node_reachable = Gauge(
    "dispatcher_node_reachable",
    "Whether a qBittorrent node is reachable (1) or not (0)",
    ["node"],
)

node_score = Gauge(
    "dispatcher_node_score",
    "Last computed score for a qBittorrent node",
    ["node"],
)

# Sonarr/Radarr metrics
arr_reachable = Gauge(
    "dispatcher_arr_reachable",
    "Whether a Sonarr/Radarr instance is reachable (1) or not (0)",
    ["name", "type"],
)

# Submission metrics
submission_total = Counter(
    "dispatcher_submission_total",
    "Total number of submissions processed by the dispatcher",
    ["status"],  # accepted / rejected / failed
)


def update_node_metrics(name: str, reachable: bool, score: float | None) -> None:
    node_reachable.labels(node=name).set(1.0 if reachable else 0.0)
    if score is not None:
        node_score.labels(node=name).set(score)


def update_arr_metrics(name: str, type_: str, reachable: bool) -> None:
    arr_reachable.labels(name=name, type=type_).set(1.0 if reachable else 0.0)


def inc_submission(status: str) -> None:
    submission_total.labels(status=status).inc()
