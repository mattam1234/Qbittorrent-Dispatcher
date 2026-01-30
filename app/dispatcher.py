from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

from anyio import to_thread

from .config import AppConfig, NodeConfig
from .metrics import inc_submission, update_node_metrics
from .models import (
	NodeMetrics,
	SubmitRequest,
	SubmitDecision,
	NodeStatus,
	DecisionDebug,
	DecisionRecord,
)
from .qb_client import QbittorrentNodeClient, NodeState
from .request_tracker import RequestTracker
from .messaging import MessagingService
from .quality_checker import QualityProfileChecker
from .n8n_client import N8nClient

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
		self._history: Deque[DecisionRecord] = deque(maxlen=200)
		
		# Initialize new services
		self.request_tracker = RequestTracker() if config.request_tracking.enabled else None
		self.messaging = MessagingService(config.integrations.messaging_services)
		self.quality_checker = QualityProfileChecker(config.arr_instances)
		self.n8n_client = N8nClient(config.integrations.n8n)

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

	def _score_node(
		self,
		node: NodeConfig,
		state: NodeState,
		metrics: NodeMetrics,
		size_estimate_gb: float = 0.0,
	) -> ScoredNode:
		settings = self.config.dispatcher

		excluded = False
		reason: Optional[str] = None

		free_disk_gb = state.free_disk_gb
		if size_estimate_gb and free_disk_gb is not None:
			# Treat estimated size as already allocated when scoring/validating.
			free_disk_gb = max(0.0, free_disk_gb - size_estimate_gb)
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
			base_score = (
				(free_disk_gb or 0.0) * settings.disk_weight
				- state.active_downloads * settings.download_weight
				- state.global_download_rate_mbps * settings.bandwidth_weight
			)
			# Apply per-node weight as a simple multiplier.
			score = base_score * (node.weight or 1.0)

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

	async def evaluate_nodes(self, size_estimate_gb: float = 0.0) -> List[ScoredNode]:
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
			scored_node = self._score_node(
				node,
				state,
				metrics,
				size_estimate_gb=size_estimate_gb,
			)
			scored.append(scored_node)

		# push metrics to Prometheus gauges
		for s in scored:
			update_node_metrics(s.config.name, s.metrics.reachable, s.score)

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
		# Check for duplicates if enabled
		if self.request_tracker and self.config.request_tracking.check_duplicates:
			is_duplicate, existing = self.request_tracker.is_duplicate(req)
			if is_duplicate and existing:
				logger.info(
					"Duplicate request detected",
					extra={"name": req.name, "existing": existing.name},
				)
				
				# Notify about duplicate
				await self.messaging.send_notification(
					f"Duplicate download detected: {req.name}\nAlready downloading: {existing.name}",
					title="Duplicate Download",
					level="warning",
				)
				
				await self.n8n_client.notify_duplicate_detected(
					req.name, req.category, existing.name
				)
				
				decision = SubmitDecision(
					selected_node=existing.selected_node,
					reason=f"duplicate_of_existing_request: {existing.name}",
					status="rejected",
					attempted_nodes=[],
				)
				inc_submission(decision.status)
				self._record_decision(req, decision)
				return decision
		
		# Check quality profiles if enabled
		if self.config.request_tracking.check_quality_profiles:
			quality_suggestion = await self.quality_checker.check_quality_match(
				req.name, req.category, req.size_estimate_gb
			)
			if quality_suggestion and self.config.request_tracking.send_suggestions:
				logger.info(
					"Quality suggestion available",
					extra={
						"name": req.name,
						"current": quality_suggestion.current_quality,
						"suggested": quality_suggestion.suggested_quality,
					},
				)
				
				# Send suggestion notification
				await self.messaging.send_notification(
					f"Better quality available for: {req.name}\n"
					f"Current: {quality_suggestion.current_quality}\n"
					f"Suggested: {quality_suggestion.suggested_quality}\n"
					f"Reason: {quality_suggestion.reason}",
					title="Quality Upgrade Suggestion",
					level="info",
				)
				
				await self.n8n_client.notify_quality_suggestion(
					req.name,
					quality_suggestion.current_quality,
					quality_suggestion.suggested_quality,
					quality_suggestion.reason,
				)
		
		scored_nodes = await self.evaluate_nodes(size_estimate_gb=req.size_estimate_gb)

		eligible = [n for n in scored_nodes if not n.excluded and n.score is not None]
		eligible.sort(key=lambda n: n.score or 0.0, reverse=True)

		attempted_metrics: List[NodeMetrics] = [n.metrics for n in scored_nodes]

		if not eligible:
			logger.warning("No eligible nodes for submission", extra={"request": req.model_dump()})
			decision = SubmitDecision(
				selected_node=None,
				reason="no_eligible_nodes",
				status="rejected",
				attempted_nodes=attempted_metrics,
			)
			inc_submission(decision.status)
			self._record_decision(req, decision)
			
			# Notify about rejection
			await self.messaging.send_notification(
				f"Download rejected - no eligible nodes: {req.name}",
				title="Download Rejected",
				level="error",
			)
			
			return decision

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

				decision = SubmitDecision(
					selected_node=node.config.name,
					reason="highest_score",
					status="accepted",
					attempted_nodes=attempted_metrics,
				)
				inc_submission(decision.status)
				self._record_decision(req, decision)
				
				# Track the request if enabled
				if self.request_tracker:
					self.request_tracker.add_request(
						req,
						source=req.category,
						selected_node=node.config.name,
					)
					self.request_tracker.update_status(
						self.request_tracker._generate_request_id(req.magnet),
						"downloading",
						node.config.name,
					)
				
				# Send success notification
				await self.messaging.send_notification(
					f"Download started on {node.config.name}: {req.name}\n"
					f"Category: {req.category}\n"
					f"Size: {req.size_estimate_gb:.2f} GB",
					title="Download Started",
					level="success",
				)
				
				# Notify n8n
				await self.n8n_client.notify_download_started(
					req.name, req.category, req.size_estimate_gb, node.config.name
				)
				
				return decision
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

		decision = SubmitDecision(
			selected_node=None,
			reason=f"submission_failed_all_nodes: {last_error}",
			status="failed",
			attempted_nodes=attempted_metrics,
		)
		inc_submission(decision.status)
		self._record_decision(req, decision)
		return decision

	def _record_decision(self, req: SubmitRequest, decision: SubmitDecision) -> None:
		"""Append a DecisionRecord to the in-memory history buffer."""

		record = DecisionRecord(
			timestamp=time.time(),
			request_name=req.name,
			request_category=req.category,
			size_estimate_gb=req.size_estimate_gb,
			selected_node=decision.selected_node,
			reason=decision.reason,
			status=decision.status,
			attempted_nodes=decision.attempted_nodes,
		)
		self._history.append(record)

	def get_decisions(self, limit: int = 50) -> List[DecisionRecord]:
		"""Return the most recent routing decisions, newest last."""

		if limit <= 0:
			return []
		# deque keeps order oldest -> newest
		items = list(self._history)
		if len(items) <= limit:
			return items
		return items[-limit:]

