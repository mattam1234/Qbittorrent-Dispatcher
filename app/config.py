from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class SubmissionSettings:
	max_retries: int = 2
	save_path: Optional[str] = None


@dataclass
class DispatcherSettings:
	disk_weight: float = 1.0
	download_weight: float = 2.0
	bandwidth_weight: float = 0.1
	max_downloads: int = 50
	min_score: float = -1.0
	admin_api_key: Optional[str] = None
	submission: SubmissionSettings = field(default_factory=SubmissionSettings)


@dataclass
class NodeConfig:
	name: str
	url: str
	username: str
	password: str
	min_free_gb: float = 0.0
	weight: float = 1.0


@dataclass
class ArrInstanceConfig:
	name: str
	type: str  # "sonarr" or "radarr"
	url: str   # Base URL pointing at the *arr API root (e.g. http://host:8989/api/v3)
	api_key: str


@dataclass
class MessagingServiceConfig:
	name: str
	type: str  # discord, slack, telegram, etc.
	webhook_url: Optional[str] = None
	bot_token: Optional[str] = None
	chat_id: Optional[str] = None
	enabled: bool = True


@dataclass
class N8nConfig:
	enabled: bool = False
	webhook_url: Optional[str] = None
	api_key: Optional[str] = None


@dataclass
class OverseerrConfig:
	enabled: bool = False
	url: str = ""
	api_key: str = ""


@dataclass
class JellyseerrConfig:
	enabled: bool = False
	url: str = ""
	api_key: str = ""


@dataclass
class ProwlarrConfig:
	enabled: bool = False
	url: str = ""
	api_key: str = ""


@dataclass
class IntegrationsConfig:
	n8n: N8nConfig = field(default_factory=N8nConfig)
	messaging_services: List[MessagingServiceConfig] = field(default_factory=list)
	overseerr: OverseerrConfig = field(default_factory=OverseerrConfig)
	jellyseerr: JellyseerrConfig = field(default_factory=JellyseerrConfig)
	prowlarr: ProwlarrConfig = field(default_factory=ProwlarrConfig)


@dataclass
class RequestTrackingConfig:
	enabled: bool = True
	check_duplicates: bool = True
	check_quality_profiles: bool = True
	send_suggestions: bool = True


@dataclass
class AppConfig:
	dispatcher: DispatcherSettings
	nodes: List[NodeConfig]
	arr_instances: List[ArrInstanceConfig] = field(default_factory=list)
	integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)
	request_tracking: RequestTrackingConfig = field(default_factory=RequestTrackingConfig)


def parse_config(raw: dict) -> AppConfig:
	dispatcher_raw = raw.get("dispatcher", {}) or {}
	submission_raw = dispatcher_raw.get("submission", {}) or {}

	submission = SubmissionSettings(
		max_retries=int(submission_raw.get("max_retries", 2)),
		save_path=submission_raw.get("save_path"),
	)

	dispatcher = DispatcherSettings(
		disk_weight=float(dispatcher_raw.get("disk_weight", 1.0)),
		download_weight=float(dispatcher_raw.get("download_weight", 2.0)),
		bandwidth_weight=float(dispatcher_raw.get("bandwidth_weight", 0.1)),
		max_downloads=int(dispatcher_raw.get("max_downloads", 50)),
		min_score=float(dispatcher_raw.get("min_score", -1.0)),
		admin_api_key=dispatcher_raw.get("admin_api_key"),
		submission=submission,
	)

	nodes_raw = raw.get("nodes", []) or []
	nodes: List[NodeConfig] = []
	for node in nodes_raw:
		nodes.append(
			NodeConfig(
				name=str(node["name"]),
				url=str(node["url"]),
				username=str(node["username"]),
				password=str(node["password"]),
				min_free_gb=float(node.get("min_free_gb", 0.0)),
				weight=float(node.get("weight", 1.0)),
			),
		)

	arr_raw = raw.get("arr_instances", []) or []
	arr_instances: List[ArrInstanceConfig] = []
	for inst in arr_raw:
		arr_instances.append(
			ArrInstanceConfig(
				name=str(inst["name"]),
				type=str(inst.get("type", "sonarr")),
				url=str(inst["url"]),
				api_key=str(inst["api_key"]),
			),
		)

	# Parse integrations config
	integrations_raw = raw.get("integrations", {}) or {}
	
	# Parse N8n config
	n8n_raw = integrations_raw.get("n8n", {}) or {}
	n8n = N8nConfig(
		enabled=bool(n8n_raw.get("enabled", False)),
		webhook_url=n8n_raw.get("webhook_url"),
		api_key=n8n_raw.get("api_key"),
	)
	
	# Parse messaging services
	messaging_raw = integrations_raw.get("messaging_services", []) or []
	messaging_services: List[MessagingServiceConfig] = []
	for svc in messaging_raw:
		messaging_services.append(
			MessagingServiceConfig(
				name=str(svc["name"]),
				type=str(svc["type"]),
				webhook_url=svc.get("webhook_url"),
				bot_token=svc.get("bot_token"),
				chat_id=svc.get("chat_id"),
				enabled=bool(svc.get("enabled", True)),
			),
		)
	
	# Parse Overseerr config
	overseerr_raw = integrations_raw.get("overseerr", {}) or {}
	overseerr = OverseerrConfig(
		enabled=bool(overseerr_raw.get("enabled", False)),
		url=overseerr_raw.get("url", ""),
		api_key=overseerr_raw.get("api_key", ""),
	)
	
	# Parse Jellyseerr config
	jellyseerr_raw = integrations_raw.get("jellyseerr", {}) or {}
	jellyseerr = JellyseerrConfig(
		enabled=bool(jellyseerr_raw.get("enabled", False)),
		url=jellyseerr_raw.get("url", ""),
		api_key=jellyseerr_raw.get("api_key", ""),
	)
	
	# Parse Prowlarr config
	prowlarr_raw = integrations_raw.get("prowlarr", {}) or {}
	prowlarr = ProwlarrConfig(
		enabled=bool(prowlarr_raw.get("enabled", False)),
		url=prowlarr_raw.get("url", ""),
		api_key=prowlarr_raw.get("api_key", ""),
	)
	
	integrations = IntegrationsConfig(
		n8n=n8n,
		messaging_services=messaging_services,
		overseerr=overseerr,
		jellyseerr=jellyseerr,
		prowlarr=prowlarr,
	)
	
	# Parse request tracking config
	tracking_raw = raw.get("request_tracking", {}) or {}
	request_tracking = RequestTrackingConfig(
		enabled=bool(tracking_raw.get("enabled", True)),
		check_duplicates=bool(tracking_raw.get("check_duplicates", True)),
		check_quality_profiles=bool(tracking_raw.get("check_quality_profiles", True)),
		send_suggestions=bool(tracking_raw.get("send_suggestions", True)),
	)

	if not nodes:
		raise ValueError("No nodes configured in config")

	return AppConfig(
		dispatcher=dispatcher, 
		nodes=nodes, 
		arr_instances=arr_instances,
		integrations=integrations,
		request_tracking=request_tracking,
	)


def load_config(path: Path | str) -> AppConfig:
	path = Path(path)
	with path.open("r", encoding="utf-8") as f:
		raw = yaml.safe_load(f) or {}
	return parse_config(raw)

