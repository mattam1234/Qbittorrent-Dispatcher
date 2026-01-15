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
	submission: SubmissionSettings = field(default_factory=SubmissionSettings)


@dataclass
class NodeConfig:
	name: str
	url: str
	username: str
	password: str
	min_free_gb: float = 0.0


@dataclass
class ArrInstanceConfig:
	name: str
	type: str  # "sonarr" or "radarr"
	url: str   # Base URL pointing at the *arr API root (e.g. http://host:8989/api/v3)
	api_key: str


@dataclass
class AppConfig:
	dispatcher: DispatcherSettings
	nodes: List[NodeConfig]
	arr_instances: List[ArrInstanceConfig] = field(default_factory=list)


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

	if not nodes:
		raise ValueError("No nodes configured in config")

	return AppConfig(dispatcher=dispatcher, nodes=nodes, arr_instances=arr_instances)


def load_config(path: Path | str) -> AppConfig:
	path = Path(path)
	with path.open("r", encoding="utf-8") as f:
		raw = yaml.safe_load(f) or {}
	return parse_config(raw)

