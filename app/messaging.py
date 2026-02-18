from __future__ import annotations

import logging
from typing import Optional

import httpx

from .config import MessagingServiceConfig

logger = logging.getLogger(__name__)


class MessagingService:
	"""Service for sending notifications via various messaging platforms."""

	def __init__(self, services: list[MessagingServiceConfig]) -> None:
		self.services = [svc for svc in services if svc.enabled]

	async def send_notification(
		self,
		message: str,
		title: Optional[str] = None,
		level: str = "info",
	) -> None:
		"""
		Send a notification to all configured messaging services.
		
		Args:
			message: The notification message
			title: Optional title for the notification
			level: Notification level (info, warning, error, success)
		"""
		if not self.services:
			logger.debug("No messaging services configured")
			return

		for service in self.services:
			try:
				await self._send_to_service(service, message, title, level)
			except Exception as exc:  # noqa: BLE001
				logger.error(
					f"Failed to send notification via {service.name}",
					extra={"service": service.name, "error": str(exc)},
				)

	async def _send_to_service(
		self,
		service: MessagingServiceConfig,
		message: str,
		title: Optional[str],
		level: str,
	) -> None:
		"""Send notification to a specific service."""
		if service.type == "discord":
			await self._send_discord(service, message, title, level)
		elif service.type == "slack":
			await self._send_slack(service, message, title, level)
		elif service.type == "telegram":
			await self._send_telegram(service, message, title, level)
		else:
			logger.warning(f"Unsupported messaging service type: {service.type}")

	async def _send_discord(
		self,
		service: MessagingServiceConfig,
		message: str,
		title: Optional[str],
		level: str,
	) -> None:
		"""Send notification to Discord via webhook."""
		if not service.webhook_url:
			logger.warning(f"Discord service {service.name} missing webhook_url")
			return

		# Map level to Discord color
		color_map = {
			"info": 0x3B82F6,      # blue
			"success": 0x10B981,   # green
			"warning": 0xF59E0B,   # amber
			"error": 0xEF4444,     # red
		}
		color = color_map.get(level, 0x3B82F6)

		payload = {
			"embeds": [
				{
					"title": title or "Qbittorrent Dispatcher Notification",
					"description": message,
					"color": color,
				}
			]
		}

		async with httpx.AsyncClient(timeout=10.0) as client:
			resp = await client.post(service.webhook_url, json=payload)
			resp.raise_for_status()
			logger.info(f"Sent notification to Discord ({service.name})")

	async def _send_slack(
		self,
		service: MessagingServiceConfig,
		message: str,
		title: Optional[str],
		level: str,
	) -> None:
		"""Send notification to Slack via webhook."""
		if not service.webhook_url:
			logger.warning(f"Slack service {service.name} missing webhook_url")
			return

		# Map level to Slack color
		color_map = {
			"info": "#3B82F6",
			"success": "#10B981",
			"warning": "#F59E0B",
			"error": "#EF4444",
		}
		color = color_map.get(level, "#3B82F6")

		payload = {
			"attachments": [
				{
					"color": color,
					"title": title or "Qbittorrent Dispatcher Notification",
					"text": message,
				}
			]
		}

		async with httpx.AsyncClient(timeout=10.0) as client:
			resp = await client.post(service.webhook_url, json=payload)
			resp.raise_for_status()
			logger.info(f"Sent notification to Slack ({service.name})")

	async def _send_telegram(
		self,
		service: MessagingServiceConfig,
		message: str,
		title: Optional[str],
		level: str,
	) -> None:
		"""Send notification to Telegram via bot API."""
		if not service.bot_token or not service.chat_id:
			logger.warning(
				f"Telegram service {service.name} missing bot_token or chat_id"
			)
			return

		# Format message with title
		full_message = f"*{title}*\n\n{message}" if title else message

		# Add emoji based on level
		emoji_map = {
			"info": "ℹ️",
			"success": "✅",
			"warning": "⚠️",
			"error": "❌",
		}
		emoji = emoji_map.get(level, "ℹ️")
		full_message = f"{emoji} {full_message}"

		url = f"https://api.telegram.org/bot{service.bot_token}/sendMessage"
		payload = {
			"chat_id": service.chat_id,
			"text": full_message,
			"parse_mode": "Markdown",
		}

		async with httpx.AsyncClient(timeout=10.0) as client:
			resp = await client.post(url, json=payload)
			resp.raise_for_status()
			logger.info(f"Sent notification to Telegram ({service.name})")
