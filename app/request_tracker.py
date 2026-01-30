from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from collections import defaultdict

from .models import SubmitRequest

logger = logging.getLogger(__name__)


@dataclass
class TrackedRequest:
	"""Represents a tracked download request."""
	name: str
	category: str
	size_estimate_gb: float
	magnet: str
	timestamp: datetime
	source: Optional[str] = None  # Which arr or user submitted it
	quality_profile: Optional[str] = None
	selected_node: Optional[str] = None
	status: str = "pending"  # pending, downloading, completed, failed


class RequestTracker:
	"""
	Service for tracking download requests to prevent duplicates 
	and manage centralized request history.
	"""

	def __init__(self) -> None:
		self._requests: Dict[str, TrackedRequest] = {}
		self._by_category: Dict[str, List[str]] = defaultdict(list)

	def add_request(
		self,
		req: SubmitRequest,
		source: Optional[str] = None,
		quality_profile: Optional[str] = None,
		selected_node: Optional[str] = None,
	) -> str:
		"""
		Add a new request to tracking.
		Returns a unique identifier for the request.
		"""
		# Generate a unique key based on magnet link (infohash)
		request_id = self._generate_request_id(req.magnet)
		
		tracked = TrackedRequest(
			name=req.name,
			category=req.category,
			size_estimate_gb=req.size_estimate_gb,
			magnet=req.magnet,
			timestamp=datetime.now(),
			source=source,
			quality_profile=quality_profile,
			selected_node=selected_node,
			status="pending",
		)
		
		self._requests[request_id] = tracked
		self._by_category[req.category].append(request_id)
		
		logger.info(
			"Tracked new request",
			extra={
				"request_id": request_id,
				"name": req.name,
				"category": req.category,
				"source": source,
			},
		)
		
		return request_id

	def is_duplicate(self, req: SubmitRequest) -> tuple[bool, Optional[TrackedRequest]]:
		"""
		Check if a request is a duplicate of an existing tracked request.
		Returns (is_duplicate, existing_request).
		"""
		request_id = self._generate_request_id(req.magnet)
		
		if request_id in self._requests:
			existing = self._requests[request_id]
			# Check if it's a recent request (within last 24 hours)
			if datetime.now() - existing.timestamp < timedelta(hours=24):
				logger.info(
					"Duplicate request detected",
					extra={
						"request_id": request_id,
						"name": req.name,
						"existing_name": existing.name,
					},
				)
				return True, existing
		
		return False, None

	def update_status(self, request_id: str, status: str, selected_node: Optional[str] = None) -> None:
		"""Update the status of a tracked request."""
		if request_id in self._requests:
			self._requests[request_id].status = status
			if selected_node:
				self._requests[request_id].selected_node = selected_node
			logger.info(
				"Updated request status",
				extra={"request_id": request_id, "status": status, "node": selected_node},
			)

	def get_request(self, request_id: str) -> Optional[TrackedRequest]:
		"""Get a tracked request by ID."""
		return self._requests.get(request_id)

	def get_all_requests(self) -> List[TrackedRequest]:
		"""Get all tracked requests."""
		return list(self._requests.values())

	def get_requests_by_category(self, category: str) -> List[TrackedRequest]:
		"""Get all requests for a specific category."""
		request_ids = self._by_category.get(category, [])
		return [self._requests[rid] for rid in request_ids if rid in self._requests]

	def cleanup_old_requests(self, days: int = 7) -> int:
		"""
		Remove requests older than specified days.
		Returns the number of requests removed.
		"""
		cutoff = datetime.now() - timedelta(days=days)
		to_remove = [
			req_id
			for req_id, req in self._requests.items()
			if req.timestamp < cutoff
		]
		
		for req_id in to_remove:
			req = self._requests[req_id]
			del self._requests[req_id]
			if req.category in self._by_category:
				if req_id in self._by_category[req.category]:
					self._by_category[req.category].remove(req_id)
		
		if to_remove:
			logger.info(f"Cleaned up {len(to_remove)} old requests")
		
		return len(to_remove)

	def _generate_request_id(self, magnet: str) -> str:
		"""
		Generate a unique identifier from a magnet link.
		Extracts the infohash from the magnet link.
		"""
		# Extract infohash from magnet link
		# Format: magnet:?xt=urn:btih:INFOHASH...
		if "btih:" in magnet:
			parts = magnet.split("btih:")
			if len(parts) > 1:
				infohash = parts[1].split("&")[0]
				return infohash[:40]  # Standard infohash length
		
		# Fallback: use hash of magnet link
		import hashlib
		return hashlib.sha1(magnet.encode()).hexdigest()
