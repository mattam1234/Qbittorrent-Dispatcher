from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from anyio import to_thread

from .config import AppConfig, NodeConfig
from .models import NodeMetrics, SubmitRequest, SubmitDecision, NodeStatus, DecisionDebug
from .qb_client import QbittorrentNodeClient, NodeState

logger = logging.getLogger(__name__)


@dataclass
class ScoredNode:
	config: NodeConfig
	client: QbittorrentNodeClient
	state: Optional[NodeState]
	metrics: NodeMetrics
	score: Optional[float]
	excluded: bool


class Dispatcher:
	def __init__(self, config: AppConfig) -> None:
		self.config = config
		self._clients = {n.name: QbittorrentNodeClient(n) for n in config.nodes}

	async def _gather_node_state(self, node: NodeConfig) -> Tuple[NodeConfig, Optional[NodeState], NodeMetrics]:
		client = self._clients[node.name]

		try:
			state = await to_thread.run_sync(client.fetch_state)
			reachable = True
			excluded_reason: Optional[str] = None
		except Exception as exc:  # noqa: BLE001
			logger.warning(
				"Node unreachable", extra={"node": node.name, "error": str(exc)}
			)
			state = None
			reachable = False
			excluded_reason = "api_unreachable"

		if state is None:
			metrics = NodeMetrics(
				name=node.name,
				free_disk_gb=None,
				active_downloads=0,
				paused_downloads=0,
				global_download_rate_mbps=0.0,
				reachable=reachable,
				excluded_reason=excluded_reason,
				score=None,
			)
			return node, None, metrics

		metrics = NodeMetrics(
			name=node.name,
			free_disk_gb=state.free_disk_gb,
			active_downloads=state.active_downloads,
			paused_downloads=state.paused_downloads,
			global_download_rate_mbps=state.global_download_rate_mbps,
			reachable=reachable,
			excluded_reason=None,
			score=None,
		)

		return node, state, metrics

	def _score_node(self, node: NodeConfig, state: NodeState, metrics: NodeMetrics) -> ScoredNode:
		settings = self.config.dispatcher

		excluded = False
		reason: Optional[str] = None

		free_disk_gb = state.free_disk_gb
		if free_disk_gb is None:
			excluded = True
			reason = "missing_free_space"
		elif free_disk_gb < node.min_free_gb:
			excluded = True
			reason = "below_min_free_space"

		if state.active_downloads > settings.max_downloads:
			excluded = True
			reason = reason or "too_many_downloads"

		score: Optional[float] = None
		if not excluded:
			score = (
				(free_disk_gb or 0.0) * settings.disk_weight
				- state.active_downloads * settings.download_weight
				- state.global_download_rate_mbps * settings.bandwidth_weight
			)

			if score < settings.min_score:
				excluded = True
				reason = reason or "score_below_minimum"

		metrics.score = score
		metrics.excluded_reason = reason

		logger.info(
			"node_scored",
			extra={
				"node": node.name,
				"score": score,
				"excluded": excluded,
				"reason": reason,
				"metrics": metrics.model_dump(),
			},
		)

		return ScoredNode(
			config=node,
			client=self._clients[node.name],
			state=state,
			metrics=metrics,
			score=score,
			excluded=excluded,
		)

	async def evaluate_nodes(self) -> List[ScoredNode]:
		tasks = [self._gather_node_state(node) for node in self.config.nodes]
		results = await asyncio.gather(*tasks)

		scored: List[ScoredNode] = []
		for node, state, metrics in results:
			if not metrics.reachable:
				scored.append(
					ScoredNode(
						config=node,
						client=self._clients[node.name],
						state=None,
						metrics=metrics,
						score=None,
						excluded=True,
					)
				)
				continue

			assert state is not None
			scored.append(self._score_node(node, state, metrics))

		return scored

	async def get_node_statuses(self) -> List[NodeStatus]:
		"""Return current metrics and exclusion flags for all nodes."""

		scored = await self.evaluate_nodes()
		return [NodeStatus(metrics=s.metrics, excluded=s.excluded) for s in scored]

	async def debug_decision(self, req: SubmitRequest) -> DecisionDebug:
		"""Evaluate nodes and show which would be selected without submitting."""

		scored_nodes = await self.evaluate_nodes()
		eligible = [n for n in scored_nodes if not n.excluded and n.score is not None]
		eligible.sort(key=lambda n: n.score or 0.0, reverse=True)

		selected: Optional[str] = eligible[0].config.name if eligible else None
		if not eligible:
			reason = "no_eligible_nodes"
		else:
			reason = "highest_score"

		statuses = [NodeStatus(metrics=s.metrics, excluded=s.excluded) for s in scored_nodes]
		return DecisionDebug(selected_node=selected, reason=reason, nodes=statuses)

	async def submit(self, req: SubmitRequest) -> SubmitDecision:
		scored_nodes = await self.evaluate_nodes()

		eligible = [n for n in scored_nodes if not n.excluded and n.score is not None]
		eligible.sort(key=lambda n: n.score or 0.0, reverse=True)

		attempted_metrics: List[NodeMetrics] = [n.metrics for n in scored_nodes]

		if not eligible:
			logger.warning("No eligible nodes for submission", extra={"request": req.model_dump()})
			return SubmitDecision(
				selected_node=None,
				reason="no_eligible_nodes",
				status="rejected",
				attempted_nodes=attempted_metrics,
			)

		max_retries = max(1, self.config.dispatcher.submission.max_retries)

		last_error: Optional[str] = None
		for attempt, node in enumerate(eligible[:max_retries], start=1):
			logger.info(
				"submission_attempt",
				extra={
					"attempt": attempt,
					"node": node.config.name,
					"request": req.model_dump(),
				},
			)
			try:
				torrent_hash = await to_thread.run_sync(
					node.client.submit_magnet,
					req.magnet,
					req.category,
					self.config.dispatcher.submission.save_path,
				)

				logger.info(
					"submission_success",
					extra={
						"node": node.config.name,
						"torrent_hash": torrent_hash,
						"request": req.model_dump(),
					},
				)

				return SubmitDecision(
					selected_node=node.config.name,
					reason="highest_score",
					status="accepted",
					attempted_nodes=attempted_metrics,
				)
			except Exception as exc:  # noqa: BLE001
				last_error = str(exc)
				logger.exception(
					"submission_failed",
					extra={
						"node": node.config.name,
						"attempt": attempt,
						"error": last_error,
					},
				)

		return SubmitDecision(
			selected_node=None,
			reason=f"submission_failed_all_nodes: {last_error}",
			status="failed",
			attempted_nodes=attempted_metrics,
		)

