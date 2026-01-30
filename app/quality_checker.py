from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import httpx

from .config import ArrInstanceConfig

logger = logging.getLogger(__name__)


@dataclass
class QualityProfile:
	"""Represents a quality profile from an ARR service."""
	id: int
	name: str
	cutoff: int
	items: List[Dict[str, Any]]
	upgrade_allowed: bool = True


@dataclass
class QualitySuggestion:
	"""Represents a quality upgrade suggestion."""
	current_quality: str
	suggested_quality: str
	reason: str
	profile_name: str


class QualityProfileChecker:
	"""
	Service for checking quality profiles and suggesting better matches
	based on ARR stack configurations.
	"""

	def __init__(self, arr_instances: List[ArrInstanceConfig]) -> None:
		self.arr_instances = arr_instances
		self._profile_cache: Dict[str, List[QualityProfile]] = {}

	async def fetch_quality_profiles(self, arr_instance: ArrInstanceConfig) -> List[QualityProfile]:
		"""Fetch quality profiles from an ARR instance."""
		cache_key = f"{arr_instance.name}:{arr_instance.type}"
		
		# Return cached profiles if available
		if cache_key in self._profile_cache:
			return self._profile_cache[cache_key]

		base_url = arr_instance.url.rstrip("/")
		url = f"{base_url}/qualityprofile"
		headers = {"X-Api-Key": arr_instance.api_key}

		try:
			async with httpx.AsyncClient(timeout=10.0) as client:
				resp = await client.get(url, headers=headers)
				resp.raise_for_status()
				data = resp.json()

				profiles = []
				for item in data:
					profiles.append(
						QualityProfile(
							id=item["id"],
							name=item["name"],
							cutoff=item.get("cutoff", 0),
							items=item.get("items", []),
							upgrade_allowed=item.get("upgradeAllowed", True),
						)
					)

				# Cache the profiles
				self._profile_cache[cache_key] = profiles
				logger.info(
					f"Fetched {len(profiles)} quality profiles from {arr_instance.name}"
				)
				return profiles

		except Exception as exc:  # noqa: BLE001
			logger.error(
				f"Failed to fetch quality profiles from {arr_instance.name}: {exc}"
			)
			return []

	async def check_quality_match(
		self,
		request_name: str,
		category: str,
		size_gb: float,
	) -> Optional[QualitySuggestion]:
		"""
		Check if the requested download matches the quality profiles.
		Returns a suggestion if a better quality match is available.
		"""
		# Determine which ARR instance to check based on category
		arr_instance = self._get_arr_for_category(category)
		if not arr_instance:
			logger.debug(f"No ARR instance found for category: {category}")
			return None

		# Fetch quality profiles
		profiles = await self.fetch_quality_profiles(arr_instance)
		if not profiles:
			return None

		# Parse quality from request name
		current_quality = self._parse_quality_from_name(request_name)
		if not current_quality:
			logger.debug(f"Could not determine quality from name: {request_name}")
			return None

		# Check if a better quality is available based on profiles
		for profile in profiles:
			if not profile.upgrade_allowed:
				continue

			suggested_quality = self._find_better_quality(
				current_quality, profile, size_gb
			)
			if suggested_quality:
				return QualitySuggestion(
					current_quality=current_quality,
					suggested_quality=suggested_quality,
					reason=f"Profile '{profile.name}' allows upgrades to {suggested_quality}",
					profile_name=profile.name,
				)

		return None

	def _get_arr_for_category(self, category: str) -> Optional[ArrInstanceConfig]:
		"""Determine which ARR instance handles a given category."""
		category_lower = category.lower()
		
		# Map categories to ARR types
		if any(keyword in category_lower for keyword in ["movie", "film"]):
			arr_type = "radarr"
		elif any(keyword in category_lower for keyword in ["tv", "series", "show"]):
			arr_type = "sonarr"
		else:
			# Default to first available instance
			return self.arr_instances[0] if self.arr_instances else None

		# Find matching ARR instance
		for arr in self.arr_instances:
			if arr.type.lower() == arr_type:
				return arr

		return None

	def _parse_quality_from_name(self, name: str) -> Optional[str]:
		"""
		Parse quality information from the release name.
		Returns quality string like '1080p', '2160p', etc.
		"""
		name_upper = name.upper()
		
		# Check for common quality indicators
		quality_patterns = [
			("2160P", "2160p"),
			("4K", "2160p"),
			("UHD", "2160p"),
			("1080P", "1080p"),
			("720P", "720p"),
			("480P", "480p"),
			("BLURAY", "BluRay"),
			("WEB-DL", "WEB-DL"),
			("WEBDL", "WEB-DL"),
			("WEBRIP", "WEBRip"),
			("HDTV", "HDTV"),
		]

		for pattern, quality in quality_patterns:
			if pattern in name_upper:
				return quality

		return None

	def _find_better_quality(
		self,
		current_quality: str,
		profile: QualityProfile,
		size_gb: float,
	) -> Optional[str]:
		"""
		Determine if there's a better quality available in the profile.
		"""
		# Quality hierarchy (lower index = better quality)
		quality_hierarchy = [
			"2160p",
			"1080p",
			"720p",
			"480p",
		]

		try:
			current_index = quality_hierarchy.index(current_quality)
		except ValueError:
			return None

		# Check if profile allows better qualities
		# This is a simplified check - in reality, you'd parse profile.items
		# to get the exact allowed qualities and cutoff
		for better_quality in quality_hierarchy[:current_index]:
			# Simple heuristic: suggest upgrade if file size suggests lower quality
			if current_quality == "1080p" and size_gb < 10 and "2160p" in str(profile.items):
				return "2160p"
			elif current_quality == "720p" and size_gb < 5 and "1080p" in str(profile.items):
				return "1080p"

		return None

	async def get_all_profiles(self) -> Dict[str, List[QualityProfile]]:
		"""Get all quality profiles from all configured ARR instances."""
		all_profiles = {}
		for arr in self.arr_instances:
			profiles = await self.fetch_quality_profiles(arr)
			all_profiles[arr.name] = profiles
		return all_profiles
