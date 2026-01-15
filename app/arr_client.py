from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from .config import ArrInstanceConfig


@dataclass
class ArrInstanceState:
    reachable: bool
    version: Optional[str]
    error: Optional[str]


async def check_arr_instance(config: ArrInstanceConfig) -> ArrInstanceState:
    """Check connectivity to a Sonarr/Radarr instance.

    Expects config.url to point at the API root, e.g. http://host:8989/api/v3
    and uses X-Api-Key header auth.
    """

    base = config.url.rstrip("/")
    url = f"{base}/system/status"

    headers = {"X-Api-Key": config.api_key}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return ArrInstanceState(
                    reachable=False,
                    version=None,
                    error=f"HTTP {resp.status_code}",
                )

            data = resp.json()
            version = data.get("version") if isinstance(data, dict) else None
            return ArrInstanceState(reachable=True, version=version, error=None)
    except Exception as exc:  # noqa: BLE001
        return ArrInstanceState(reachable=False, version=None, error=str(exc))
