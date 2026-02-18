from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import httpx

from .config import OverseerrConfig, JellyseerrConfig, ProwlarrConfig

logger = logging.getLogger(__name__)


@dataclass
class MediaRequest:
	"""Represents a media request from Overseerr/Jellyseerr."""
	id: int
	media_type: str  # movie, tv
	media_id: int
	status: str
	requested_by: str
	title: str
	year: Optional[int] = None
	tvdb_id: Optional[int] = None
	tmdb_id: Optional[int] = None


class OverseerrClient:
	"""Client for interacting with Overseerr API."""

	def __init__(self, config: OverseerrConfig) -> None:
		self.config = config
		self.base_url = config.url.rstrip("/")
		self.api_key = config.api_key

	async def get_pending_requests(self) -> List[MediaRequest]:
		"""Get all pending media requests from Overseerr."""
		if not self.config.enabled:
			return []

		url = f"{self.base_url}/api/v1/request"
		headers = {"X-Api-Key": self.api_key}
		params = {"filter": "pending", "take": 50}

		try:
			async with httpx.AsyncClient(timeout=10.0) as client:
				resp = await client.get(url, headers=headers, params=params)
				resp.raise_for_status()
				data = resp.json()

				requests = []
				for item in data.get("results", []):
					year = None
					if item["media"].get("releaseDate"):
						try:
							year_str = str(item["media"]["releaseDate"])[:4]
							if year_str.isdigit() and len(year_str) == 4:
								year = int(year_str)
						except (ValueError, IndexError):
							pass
					
					requests.append(
						MediaRequest(
							id=item["id"],
							media_type=item["type"],
							media_id=item["media"]["id"],
							status=item["status"],
							requested_by=item.get("requestedBy", {}).get("displayName", "Unknown"),
							title=item["media"].get("title", "Unknown"),
							year=year,
							tvdb_id=item["media"].get("externalIds", {}).get("tvdbId"),
							tmdb_id=item["media"].get("tmdbId"),
						)
					)
				return requests
		except Exception as exc:  # noqa: BLE001
			logger.error(f"Failed to fetch Overseerr requests: {exc}")
			return []

	async def check_status(self) -> tuple[bool, Optional[str]]:
		"""Check if Overseerr is reachable."""
		if not self.config.enabled:
			return False, "Not enabled"

		url = f"{self.base_url}/api/v1/status"
		headers = {"X-Api-Key": self.api_key}

		try:
			async with httpx.AsyncClient(timeout=5.0) as client:
				resp = await client.get(url, headers=headers)
				resp.raise_for_status()
				data = resp.json()
				version = data.get("version", "unknown")
				return True, version
		except Exception as exc:  # noqa: BLE001
			return False, str(exc)


class JellyseerrClient:
	"""Client for interacting with Jellyseerr API (similar to Overseerr)."""

	def __init__(self, config: JellyseerrConfig) -> None:
		self.config = config
		self.base_url = config.url.rstrip("/")
		self.api_key = config.api_key

	async def get_pending_requests(self) -> List[MediaRequest]:
		"""Get all pending media requests from Jellyseerr."""
		if not self.config.enabled:
			return []

		url = f"{self.base_url}/api/v1/request"
		headers = {"X-Api-Key": self.api_key}
		params = {"filter": "pending", "take": 50}

		try:
			async with httpx.AsyncClient(timeout=10.0) as client:
				resp = await client.get(url, headers=headers, params=params)
				resp.raise_for_status()
				data = resp.json()

				requests = []
				for item in data.get("results", []):
					year = None
					if item["media"].get("releaseDate"):
						try:
							year_str = str(item["media"]["releaseDate"])[:4]
							if year_str.isdigit() and len(year_str) == 4:
								year = int(year_str)
						except (ValueError, IndexError):
							pass
					
					requests.append(
						MediaRequest(
							id=item["id"],
							media_type=item["type"],
							media_id=item["media"]["id"],
							status=item["status"],
							requested_by=item.get("requestedBy", {}).get("displayName", "Unknown"),
							title=item["media"].get("title", "Unknown"),
							year=year,
							tvdb_id=item["media"].get("externalIds", {}).get("tvdbId"),
							tmdb_id=item["media"].get("tmdbId"),
						)
					)
				return requests
		except Exception as exc:  # noqa: BLE001
			logger.error(f"Failed to fetch Jellyseerr requests: {exc}")
			return []

	async def check_status(self) -> tuple[bool, Optional[str]]:
		"""Check if Jellyseerr is reachable."""
		if not self.config.enabled:
			return False, "Not enabled"

		url = f"{self.base_url}/api/v1/status"
		headers = {"X-Api-Key": self.api_key}

		try:
			async with httpx.AsyncClient(timeout=5.0) as client:
				resp = await client.get(url, headers=headers)
				resp.raise_for_status()
				data = resp.json()
				version = data.get("version", "unknown")
				return True, version
		except Exception as exc:  # noqa: BLE001
			return False, str(exc)


class ProwlarrClient:
	"""Client for interacting with Prowlarr API."""

	def __init__(self, config: ProwlarrConfig) -> None:
		self.config = config
		self.base_url = config.url.rstrip("/")
		self.api_key = config.api_key

	async def get_indexers(self) -> List[Dict[str, Any]]:
		"""Get all configured indexers from Prowlarr."""
		if not self.config.enabled:
			return []

		url = f"{self.base_url}/api/v1/indexer"
		headers = {"X-Api-Key": self.api_key}

		try:
			async with httpx.AsyncClient(timeout=10.0) as client:
				resp = await client.get(url, headers=headers)
				resp.raise_for_status()
				return resp.json()
		except Exception as exc:  # noqa: BLE001
			logger.error(f"Failed to fetch Prowlarr indexers: {exc}")
			return []

	async def search(self, query: str, categories: Optional[List[int]] = None) -> List[Dict[str, Any]]:
		"""Search for torrents using Prowlarr."""
		if not self.config.enabled:
			return []

		url = f"{self.base_url}/api/v1/search"
		headers = {"X-Api-Key": self.api_key}
		params = {"query": query}
		if categories:
			params["categories"] = ",".join(map(str, categories))

		try:
			async with httpx.AsyncClient(timeout=30.0) as client:
				resp = await client.get(url, headers=headers, params=params)
				resp.raise_for_status()
				return resp.json()
		except Exception as exc:  # noqa: BLE001
			logger.error(f"Failed to search Prowlarr: {exc}")
			return []

	async def check_status(self) -> tuple[bool, Optional[str]]:
		"""Check if Prowlarr is reachable."""
		if not self.config.enabled:
			return False, "Not enabled"

		url = f"{self.base_url}/api/v1/system/status"
		headers = {"X-Api-Key": self.api_key}

		try:
			async with httpx.AsyncClient(timeout=5.0) as client:
				resp = await client.get(url, headers=headers)
				resp.raise_for_status()
				data = resp.json()
				version = data.get("version", "unknown")
				return True, version
		except Exception as exc:  # noqa: BLE001
			return False, str(exc)
