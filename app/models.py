from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SubmitRequest(BaseModel):
	name: str = Field(..., description="Human-readable title of the download")
	category: str = Field(..., description="qBittorrent category to assign")
	size_estimate_gb: float = Field(..., ge=0, description="Approximate size in GiB")
	magnet: str = Field(..., description="Magnet URI for the torrent")


class NodeMetrics(BaseModel):
	name: str
	free_disk_gb: Optional[float] = None
	active_downloads: int
	paused_downloads: int
	global_download_rate_mbps: float
	reachable: bool
	excluded_reason: Optional[str] = None
	score: Optional[float] = None


class NodeStatus(BaseModel):
	metrics: NodeMetrics
	excluded: bool


class SubmitDecision(BaseModel):
	selected_node: Optional[str]
	reason: str
	status: str
	attempted_nodes: list[NodeMetrics] = []


class DecisionDebug(BaseModel):
	selected_node: Optional[str]
	reason: str
	nodes: list[NodeStatus]


class ConfigRaw(BaseModel):
	yaml: str


class ArrStatus(BaseModel):
	name: str
	type: str
	url: str
	reachable: bool
	version: Optional[str] = None
	error: Optional[str] = None


class SubmissionConfig(BaseModel):
	max_retries: int = 2
	save_path: Optional[str] = None


class DispatcherConfig(BaseModel):
	disk_weight: float = 1.0
	download_weight: float = 2.0
	bandwidth_weight: float = 0.1
	max_downloads: int = 50
	min_score: float = -1.0
	submission: SubmissionConfig = SubmissionConfig()


class NodeConfigModel(BaseModel):
	name: str
	url: str
	username: str
	password: str
	min_free_gb: float = 0.0


class ArrInstanceModel(BaseModel):
	name: str
	type: str
	url: str
	api_key: str


class AppConfigModel(BaseModel):
	dispatcher: DispatcherConfig
	nodes: list[NodeConfigModel]
	arr_instances: list[ArrInstanceModel] = []


