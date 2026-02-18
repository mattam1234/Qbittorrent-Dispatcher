from __future__ import annotations

import logging
from typing import Optional, Dict, Any

import httpx

from .config import N8nConfig

logger = logging.getLogger(__name__)


class N8nClient:
	"""Client for integrating with n8n automation platform."""

	def __init__(self, config: N8nConfig) -> None:
		self.config = config
		self.webhook_url = config.webhook_url
		self.api_key = config.api_key

	async def trigger_webhook(
		self,
		event_type: str,
		payload: Dict[str, Any],
	) -> bool:
		"""
		Trigger an n8n webhook with the given event and payload.
		
		Args:
			event_type: Type of event (e.g., 'download_started', 'download_completed')
			payload: Event data to send to n8n
			
		Returns:
			True if webhook was triggered successfully, False otherwise
		"""
		if not self.config.enabled or not self.webhook_url:
			logger.debug("n8n integration not enabled or webhook URL not configured")
			return False

		# Add timestamp to payload
		from datetime import datetime
		
		# Prepare webhook payload
		webhook_payload = {
			"event": event_type,
			"timestamp": datetime.now().isoformat(),
			"data": payload,
		}

		# Add API key if configured
		headers = {}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"

		try:
			async with httpx.AsyncClient(timeout=10.0) as client:
				resp = await client.post(
					self.webhook_url,
					json=webhook_payload,
					headers=headers,
				)
				resp.raise_for_status()
				
				logger.info(
					f"Triggered n8n webhook for event: {event_type}",
					extra={"event": event_type, "status": resp.status_code},
				)
				return True

		except Exception as exc:  # noqa: BLE001
			logger.error(
				f"Failed to trigger n8n webhook: {exc}",
				extra={"event": event_type, "error": str(exc)},
			)
			return False

	async def notify_download_started(
		self,
		name: str,
		category: str,
		size_gb: float,
		node: str,
	) -> bool:
		"""Notify n8n that a download has started."""
		payload = {
			"name": name,
			"category": category,
			"size_gb": size_gb,
			"node": node,
			"status": "started",
		}
		return await self.trigger_webhook("download_started", payload)

	async def notify_download_completed(
		self,
		name: str,
		category: str,
		node: str,
	) -> bool:
		"""Notify n8n that a download has completed."""
		payload = {
			"name": name,
			"category": category,
			"node": node,
			"status": "completed",
		}
		return await self.trigger_webhook("download_completed", payload)

	async def notify_duplicate_detected(
		self,
		name: str,
		category: str,
		existing_name: str,
	) -> bool:
		"""Notify n8n that a duplicate download was detected."""
		payload = {
			"name": name,
			"category": category,
			"existing_name": existing_name,
			"status": "duplicate",
		}
		return await self.trigger_webhook("duplicate_detected", payload)

	async def notify_quality_suggestion(
		self,
		name: str,
		current_quality: str,
		suggested_quality: str,
		reason: str,
	) -> bool:
		"""Notify n8n about a quality upgrade suggestion."""
		payload = {
			"name": name,
			"current_quality": current_quality,
			"suggested_quality": suggested_quality,
			"reason": reason,
			"status": "suggestion",
		}
		return await self.trigger_webhook("quality_suggestion", payload)

	async def check_connection(self) -> tuple[bool, Optional[str]]:
		"""
		Check if n8n webhook is reachable.
		
		Returns:
			Tuple of (is_connected, error_message)
		"""
		if not self.config.enabled or not self.webhook_url:
			return False, "Not enabled or URL not configured"

		try:
			# Send a test ping
			test_payload = {"event": "ping", "data": {"test": True}}
			headers = {}
			if self.api_key:
				headers["Authorization"] = f"Bearer {self.api_key}"

			async with httpx.AsyncClient(timeout=5.0) as client:
				resp = await client.post(
					self.webhook_url,
					json=test_payload,
					headers=headers,
				)
				resp.raise_for_status()
				return True, None

		except Exception as exc:  # noqa: BLE001
			return False, str(exc)
