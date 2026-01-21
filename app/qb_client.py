from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import qbittorrentapi

from .config import NodeConfig

logger = logging.getLogger(__name__)


@dataclass
class NodeState:
	free_disk_gb: Optional[float]
	active_downloads: int
	paused_downloads: int
	global_download_rate_mbps: float


class QbittorrentNodeClient:
	def __init__(self, config: NodeConfig) -> None:
		self.config = config
		self._client = qbittorrentapi.Client(
			host=config.url,
			username=config.username,
			password=config.password,
		)

	def _ensure_authenticated(self) -> None:
		if not self._client.is_logged_in:
			self._client.auth_log_in()

	def fetch_state(self) -> NodeState:
		"""Fetch current metrics for the node using qBittorrent Web API.

		Uses sync.maindata, transfer.info, and torrents.info as requested.
		"""

		self._ensure_authenticated()

		try:
			maindata = self._client.sync_maindata()
			transfer_info = self._client.transfer_info()
			torrents_downloading = self._client.torrents_info(status_filter="downloading")
			torrents_paused = self._client.torrents_info(status_filter="paused")
		except Exception:
			logger.exception("Failed to fetch state from node", extra={"node": self.config.name})
			raise

		server_state = maindata.get("server_state", {}) if isinstance(maindata, dict) else {}

		free_bytes = server_state.get("free_space_on_disk") if isinstance(server_state, dict) else None
		free_disk_gb: Optional[float]
		if isinstance(free_bytes, (int, float)):
			free_disk_gb = float(free_bytes) / (1024 ** 3)
		else:
			free_disk_gb = None

		dl_speed_bytes = transfer_info.get("dl_info_speed", 0) if isinstance(transfer_info, dict) else 0
		if isinstance(dl_speed_bytes, (int, float)):
			global_download_rate_mbps = float(dl_speed_bytes) * 8.0 / 1_000_000.0
		else:
			global_download_rate_mbps = 0.0

		return NodeState(
			free_disk_gb=free_disk_gb,
			active_downloads=len(list(torrents_downloading or [])),
			paused_downloads=len(list(torrents_paused or [])),
			global_download_rate_mbps=global_download_rate_mbps,
		)

	def submit_magnet(self, magnet: str, category: str, save_path: Optional[str] = None) -> str:
		"""Submit a magnet link to this node.

		Returns the torrent hash reported by qBittorrent.
		"""

		self._ensure_authenticated()

		params = {
			"urls": magnet,
			"category": category,
			"paused": False,
		}
		if save_path:
			params["savepath"] = save_path

		try:
			self._client.torrents_add(**params)
		except Exception:
			logger.exception(
				"Failed to submit magnet to node", extra={"node": self.config.name}
			)
			raise

		# Fetch the most recent matching torrent to return its hash
		# This is a heuristic; qBittorrent does not echo the hash directly.
		try:
			torrents = self._client.torrents_info(sort="added_on", reverse=True)
			if torrents:
				return torrents[0].hash
		except Exception:
			logger.exception(
				"Failed to retrieve torrent hash after submission",
				extra={"node": self.config.name},
			)

		return ""

