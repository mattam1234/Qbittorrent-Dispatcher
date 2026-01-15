from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Form, Response
from fastapi.responses import PlainTextResponse, HTMLResponse

from .config import load_config, AppConfig, DEFAULT_CONFIG_PATH, parse_config
from .dispatcher import Dispatcher
from .models import (
	SubmitRequest,
	SubmitDecision,
	NodeStatus,
	DecisionDebug,
	ConfigRaw,
	ArrStatus,
	AppConfigModel,
	DispatcherConfig,
	SubmissionConfig,
	NodeConfigModel,
	ArrInstanceModel,
)
from .arr_client import check_arr_instance
import yaml
import asyncio


def configure_logging() -> None:
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(name)s %(message)s",
	)


def load_app_config() -> AppConfig:
	config_path = Path("config.yaml")
	if not config_path.exists():
		raise RuntimeError(f"Configuration file not found at {config_path}")
	return load_config(config_path)


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
	configure_logging()

	if config is None:
		config = load_app_config()

	config_obj = config
	dispatcher = Dispatcher(config_obj)
	app = FastAPI(title="Space-Aware qBittorrent Dispatcher")

	@app.post("/submit", response_model=SubmitDecision)
	async def submit(req: SubmitRequest) -> SubmitDecision:  # noqa: D401
		"""Submit a new download and have the dispatcher pick the best node."""

		decision = await dispatcher.submit(req)

		if decision.status == "rejected":
			raise HTTPException(status_code=503, detail=decision.model_dump())

		if decision.status == "failed":
			raise HTTPException(status_code=503, detail=decision.model_dump())

		return decision

	@app.get("/config/raw", response_class=PlainTextResponse)
	async def get_config_raw() -> str:
		"""Return the current YAML configuration file."""

		try:
			return DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
		except FileNotFoundError as exc:  # noqa: PERF203
			raise HTTPException(status_code=404, detail="config.yaml not found") from exc

	@app.post("/config/raw")
	async def update_config_raw(payload: ConfigRaw) -> dict[str, str]:
		"""Validate and persist new YAML config, then hot-reload dispatcher."""

		try:
			raw = yaml.safe_load(payload.yaml) or {}
			new_config = parse_config(raw)
		except Exception as exc:  # noqa: BLE001
			raise HTTPException(status_code=400, detail=f"Invalid config: {exc}") from exc

		try:
			DEFAULT_CONFIG_PATH.write_text(payload.yaml, encoding="utf-8")
		except Exception as exc:  # noqa: BLE001
			raise HTTPException(status_code=500, detail=f"Failed to write config: {exc}") from exc

		nonlocal config_obj, dispatcher
		config_obj = new_config
		dispatcher = Dispatcher(config_obj)

		return {"status": "ok"}

	@app.get("/config/json", response_model=AppConfigModel)
	async def get_config_json() -> AppConfigModel:
		"""Return the current configuration as structured JSON."""

		disp = config_obj.dispatcher
		sub = disp.submission
		dispatcher_cfg = DispatcherConfig(
			disk_weight=disp.disk_weight,
			download_weight=disp.download_weight,
			bandwidth_weight=disp.bandwidth_weight,
			max_downloads=disp.max_downloads,
			min_score=disp.min_score,
			submission=SubmissionConfig(
				max_retries=sub.max_retries,
				save_path=sub.save_path,
			),
		)

		nodes_cfg = [
			NodeConfigModel(
				name=n.name,
				url=n.url,
				username=n.username,
				password=n.password,
				min_free_gb=n.min_free_gb,
			)
			for n in config_obj.nodes
		]

		arr_cfg = [
			ArrInstanceModel(
				name=a.name,
				type=a.type,
				url=a.url,
				api_key=a.api_key,
			)
			for a in getattr(config_obj, "arr_instances", []) or []
		]

		return AppConfigModel(dispatcher=dispatcher_cfg, nodes=nodes_cfg, arr_instances=arr_cfg)

	@app.post("/config/json", response_model=AppConfigModel)
	async def update_config_json(payload: AppConfigModel) -> AppConfigModel:
		"""Validate and persist structured JSON config, then hot-reload dispatcher."""

		raw = payload.model_dump()
		try:
			new_config = parse_config(raw)
		except Exception as exc:  # noqa: BLE001
			raise HTTPException(status_code=400, detail=f"Invalid config: {exc}") from exc

		try:
			DEFAULT_CONFIG_PATH.write_text(
				yaml.safe_dump(raw, sort_keys=False),
				encoding="utf-8",
			)
		except Exception as exc:  # noqa: BLE001
			raise HTTPException(status_code=500, detail=f"Failed to write config: {exc}") from exc

		nonlocal config_obj, dispatcher
		config_obj = new_config
		dispatcher = Dispatcher(config_obj)

		# Return the normalized config view
		return await get_config_json()

	@app.get("/", response_class=HTMLResponse)
	async def dashboard() -> str:
		"""Simple web UI to inspect node status and routing behavior."""

		return """<!DOCTYPE html>
<html lang=\"en\">
<head>
	<meta charset=\"UTF-8\" />
	<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
	<title>qBittorrent Dispatcher Dashboard</title>
	<style>
		body { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #0f172a; color: #e5e7eb; }
		header { padding: 1rem 2rem; background: #020617; border-bottom: 1px solid #1e293b; display: flex; justify-content: space-between; align-items: center; }
		main { padding: 1.5rem 2rem; }
		h1 { font-size: 1.4rem; margin: 0; }
		.pill { padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.75rem; background: #1e293b; color: #e5e7eb; }
		table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
		th, td { padding: 0.5rem 0.75rem; text-align: left; font-size: 0.85rem; }
		th { background: #020617; border-bottom: 1px solid #1f2937; position: sticky; top: 0; z-index: 1; }
		tr:nth-child(even) { background: #020617; }
		tr:nth-child(odd) { background: #020617; }
		tr:hover { background: #111827; }
		.badge { border-radius: 999px; padding: 0.15rem 0.5rem; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.03em; }
		.badge-ok { background: #16a34a33; color: #4ade80; }
		.badge-bad { background: #b91c1c33; color: #fca5a5; }
		.badge-warn { background: #ca8a0433; color: #facc15; }
		.monospace { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size: 0.8rem; }
		.layout { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1.2fr); gap: 1.5rem; align-items: flex-start; }
		.card { background: #020617; border-radius: 0.75rem; padding: 1rem 1.25rem; border: 1px solid #1e293b; box-shadow: 0 10px 20px rgba(15,23,42,0.6); }
		.card h2 { font-size: 1rem; margin: 0 0 0.5rem 0; }
		.muted { color: #9ca3af; font-size: 0.8rem; }
		label { display: block; font-size: 0.8rem; margin-top: 0.5rem; margin-bottom: 0.15rem; color: #9ca3af; }
		input, textarea { width: 100%; border-radius: 0.5rem; border: 1px solid #1f2937; padding: 0.4rem 0.55rem; background: #020617; color: #e5e7eb; font-size: 0.85rem; resize: vertical; }
		input:focus, textarea:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 1px #1d4ed8; }
		button { margin-top: 0.75rem; border-radius: 999px; padding: 0.4rem 0.9rem; border: none; font-size: 0.8rem; cursor: pointer; background: linear-gradient(to right, #2563eb, #4f46e5); color: white; box-shadow: 0 8px 16px rgba(37,99,235,0.4); }
		button:disabled { opacity: 0.6; cursor: default; box-shadow: none; }
		.small { font-size: 0.78rem; }
		.stat-row { display: flex; justify-content: space-between; margin-top: 0.25rem; font-size: 0.78rem; }
		.chip-row { display: flex; gap: 0.25rem; flex-wrap: wrap; margin-top: 0.35rem; }
	</style>
</head>
<body>
	<header>
		<div>
			<h1>qBittorrent Dispatcher</h1>
			<div class=\"muted\">Space-aware routing across multiple nodes</div>
		</div>
		<div style=\"display:flex; align-items:center; gap:0.75rem;\">
			<nav class=\"small\" style=\"display:flex; gap:0.5rem; align-items:center;\">
				<a href=\"/\" style=\"color:#9ca3af; text-decoration:none;\">Dashboard</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/config\" style=\"color:#9ca3af; text-decoration:none;\">Config</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/nodes\" style=\"color:#9ca3af; text-decoration:none;\">/nodes</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/arr\" style=\"color:#9ca3af; text-decoration:none;\">/arr</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/health\" style=\"color:#9ca3af; text-decoration:none;\">/health</a>
			</nav>
			<span id=\"global-status\" class=\"pill\">Loading...</span>
		</div>
	</header>

	<main>
		<div class=\"layout\">
			<section class=\"card\">
				<h2>Nodes</h2>
				<div class=\"muted\">Live metrics from all configured qBittorrent nodes.</div>
				<table>
					<thead>
						<tr>
							<th>Name</th>
							<th>Free (GiB)</th>
							<th>Active</th>
							<th>Paused</th>
							<th>DL (Mbps)</th>
							<th>Score</th>
							<th>Status</th>
						</tr>
					</thead>
					<tbody id=\"nodes-body\"></tbody>
				</table>
				<div class=\"muted small\" style=\"margin-top:0.5rem;\">Auto-refreshes every 5 seconds.</div>
			</section>

			<section class=\"card\">
				<h2>Dry-run decision</h2>
				<div class=\"muted\">Test how a request would be routed without actually submitting it.</div>
				<form id=\"debug-form\">
					<label for=\"category\">Category</label>
					<input id=\"category\" name=\"category\" placeholder=\"e.g. movies-uhd\" />

					<label for=\"name\">Name</label>
					<input id=\"name\" name=\"name\" placeholder=\"Human-readable title (optional)\" />

					<label for=\"magnet\">Magnet URI</label>
					<textarea id=\"magnet\" name=\"magnet\" rows=\"3\" placeholder=\"magnet:?xt=urn:btih:...\"></textarea>

					<label for=\"size\">Size estimate (GiB)</label>
					<input id=\"size\" name=\"size\" type=\"number\" step=\"0.1\" min=\"0\" placeholder=\"0\" />

					<button type=\"submit\" id=\"debug-button\">Run decision</button>
				</form>

				<div id=\"debug-result\" class=\"muted small\" style=\"margin-top:0.75rem; white-space:pre-wrap;\"></div>

				<hr style=\"margin:0.9rem 0;border-color:#1f2937;border-width:0;border-top-width:1px;\" />
				<div class=\"muted small\">*arr connectivity</div>
				<div id=\"arr-summary\" class=\"muted small\" style=\"margin-top:0.25rem;\">Loading...</div>
				<ul id=\"arr-list\" class=\"small\" style=\"margin-top:0.35rem; padding-left:1rem; margin-bottom:0;\"></ul>
			</section>
		</div>
	</main>

	<script>
		async function fetchNodes() {
			const body = document.getElementById('nodes-body');
			const status = document.getElementById('global-status');
			try {
				const res = await fetch('/nodes');
				if (!res.ok) throw new Error('HTTP ' + res.status);
				const data = await res.json();
				body.innerHTML = '';
				let healthyCount = 0;
				for (const node of data) {
					const m = node.metrics;
					const tr = document.createElement('tr');
					const score = m.score !== null && m.score !== undefined ? m.score.toFixed(2) : '–';
					const free = m.free_disk_gb !== null && m.free_disk_gb !== undefined ? m.free_disk_gb.toFixed(1) : '–';
					const dl = m.global_download_rate_mbps !== null && m.global_download_rate_mbps !== undefined ? m.global_download_rate_mbps.toFixed(2) : '0.00';
					const excluded = node.excluded;
					let badgeClass = 'badge badge-ok';
					let badgeText = 'eligible';
					if (!m.reachable) { badgeClass = 'badge badge-bad'; badgeText = 'unreachable'; }
					else if (excluded) { badgeClass = 'badge badge-warn'; badgeText = m.excluded_reason || 'excluded'; }
					else { healthyCount += 1; }
					tr.innerHTML = `
						<td class="monospace">${m.name}</td>
						<td>${free}</td>
						<td>${m.active_downloads}</td>
						<td>${m.paused_downloads}</td>
						<td>${dl}</td>
						<td>${score}</td>
						<td><span class="${badgeClass}">${badgeText}</span></td>
					`;
					body.appendChild(tr);
				}
				if (data.length === 0) {
					status.textContent = 'No nodes configured';
					status.style.background = '#b91c1c33';
				} else if (healthyCount === 0) {
					status.textContent = 'No eligible nodes';
					status.style.background = '#b91c1c33';
				} else {
					status.textContent = healthyCount + ' / ' + data.length + ' eligible';
					status.style.background = '#16a34a33';
				}
			} catch (err) {
				console.error(err);
				status.textContent = 'Error loading nodes';
				status.style.background = '#b91c1c33';
			}
		}

		async function fetchArr() {
			const summary = document.getElementById('arr-summary');
			const list = document.getElementById('arr-list');
			if (!summary || !list) return;
			try {
				const res = await fetch('/arr');
				if (!res.ok) throw new Error('HTTP ' + res.status);
				const data = await res.json();
				list.innerHTML = '';
				if (!Array.isArray(data) || data.length === 0) {
					summary.textContent = 'No arr_instances configured';
					return;
				}
				let reachableCount = 0;
				for (const inst of data) {
					const li = document.createElement('li');
					const badgeClass = inst.reachable ? 'badge badge-ok' : 'badge badge-bad';
					const badgeText = inst.reachable ? 'reachable' : 'unreachable';
					const ver = inst.version ? 'v' + inst.version : '';
					if (inst.reachable) reachableCount += 1;
					li.innerHTML = `
						<span class="monospace">${inst.name}</span>
						<span class="badge ${badgeClass}" style="margin-left:0.35rem;">${badgeText}</span>
						<span class="muted" style="margin-left:0.35rem; font-size:0.75rem;">${inst.type}${ver ? ' • ' + ver : ''}</span>
						${inst.error ? `<span class="muted" style="display:block; margin-left:0.2rem; font-size:0.7rem;">${inst.error}</span>` : ''}
					`;
					list.appendChild(li);
				}
				summary.textContent = `${reachableCount} / ${data.length} reachable`;
			} catch (err) {
				console.error(err);
				if (summary) summary.textContent = 'Error loading *arr status';
			}
		}

		async function runDecision(event) {
			event.preventDefault();
			const btn = document.getElementById('debug-button');
			const out = document.getElementById('debug-result');
			const category = document.getElementById('category').value || 'default';
			const name = document.getElementById('name').value || 'debug-request';
			const magnet = document.getElementById('magnet').value || 'magnet:?xt=urn:btih:debug';
			const sizeVal = parseFloat(document.getElementById('size').value || '0');
			const size = isNaN(sizeVal) ? 0 : sizeVal;

			btn.disabled = true;
			out.textContent = 'Running decision...';
			try {
				const res = await fetch('/debug/decision', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ name, category, size_estimate_gb: size, magnet })
				});
				if (!res.ok) {
					out.textContent = 'Error: ' + res.status + ' ' + (await res.text());
				} else {
					const data = await res.json();
					let text = '';
					text += 'Selected node: ' + (data.selected_node || 'none') + '\n';
					text += 'Reason: ' + data.reason + '\n\n';
					text += 'Nodes:\n';
					for (const ns of data.nodes) {
						const m = ns.metrics;
						text += `- ${m.name} | score=${m.score ?? '–'} | eligible=${!ns.excluded} | reason=${m.excluded_reason || ''}\n`;
					}
					out.textContent = text;
				}
			} catch (err) {
				console.error(err);
				out.textContent = 'Request failed: ' + err;
			} finally {
				btn.disabled = false;
			}
		}

		document.getElementById('debug-form').addEventListener('submit', runDecision);
		fetchNodes();
			fetchArr();
			setInterval(fetchNodes, 5000);
			setInterval(fetchArr, 10000);
	</script>
</body>
</html>"""

	@app.get("/nodes", response_model=list[NodeStatus])
	async def list_nodes() -> list[NodeStatus]:
		"""Return current node metrics, scores, and exclusion flags."""

		return await dispatcher.get_node_statuses()

	# --- qBittorrent-compatible endpoints for Sonarr/Radarr ---

	@app.post("/api/v2/auth/login", response_class=PlainTextResponse)
	async def qb_login(
		username: str = Form(""),  # noqa: ARG001
		password: str = Form(""),  # noqa: ARG001
		response: Response = None,
	) -> str:
		"""Fake qBittorrent login; accepts any credentials.

		Sonarr/Radarr expect this endpoint to exist when configured
		as a qBittorrent download client. We don't enforce auth but
		return "Ok." and a dummy SID cookie.
		"""

		if response is None:
			response = Response()
		response.set_cookie("SID", "dispatcher", httponly=True, path="/")
		return "Ok."

	@app.post("/api/v2/torrents/add", response_class=PlainTextResponse)
	async def qb_torrents_add(
		urls: str = Form(""),
		category: str = Form(""),
		savepath: str = Form(""),
	) -> str:
		"""qBittorrent-compatible add endpoint used by Sonarr/Radarr.

		We only support magnet URLs via the `urls` field and route
		the submission through the dispatcher.
		"""

		if not urls:
			raise HTTPException(status_code=400, detail="No urls provided")

		magnet = urls.strip().splitlines()[0].strip()
		if not magnet.startswith("magnet:"):
			raise HTTPException(status_code=400, detail="Only magnet URLs are supported")

		# Fallbacks if category/savepath are empty; dispatcher config
		# can still override save_path globally.
		normalized_category = category or ""

		req = SubmitRequest(
			name=magnet,
			category=normalized_category or "default",
			size_estimate_gb=0.0,
			magnet=magnet,
		)

		decision = await dispatcher.submit(req)

		if decision.status != "accepted":
			raise HTTPException(status_code=503, detail=decision.model_dump())

		return "Ok."

	@app.post("/debug/decision", response_model=DecisionDebug)
	async def debug_decision(req: SubmitRequest) -> DecisionDebug:
		"""Dry-run a decision: score nodes but do not submit the torrent."""

		return await dispatcher.debug_decision(req)

	@app.get("/api/v2/app/version", response_class=PlainTextResponse)
	async def qb_app_version() -> str:
		"""Minimal version endpoint so *arr clients detect qBittorrent."""

		return "dispatcher-1.0.0"

	@app.get("/api/v2/app/webapiVersion", response_class=PlainTextResponse)
	async def qb_webapi_version() -> str:
		"""Report a qBittorrent-compatible Web API version string."""

		return "2.8.18"

	@app.get("/health")
	async def health() -> dict[str, str]:
		return {"status": "ok"}

	@app.get("/arr", response_model=list[ArrStatus])
	async def arr_status() -> list[ArrStatus]:
		"""Return connectivity status for configured Sonarr/Radarr instances."""

		instances = getattr(config_obj, "arr_instances", []) or []
		if not instances:
			return []

		results = await asyncio.gather(*(check_arr_instance(inst) for inst in instances))
		out: list[ArrStatus] = []
		for inst, state in zip(instances, results, strict=False):
			out.append(
				ArrStatus(
					name=inst.name,
					type=inst.type,
					url=inst.url,
					reachable=state.reachable,
					version=state.version,
					error=state.error,
				),
			)
		return out

	@app.get("/config", response_class=HTMLResponse)
	async def config_ui() -> str:
		"""Form-based configurator for dispatcher, nodes, and *arr instances."""

		return """<!DOCTYPE html>
<html lang=\"en\">
<head>
	<meta charset=\"UTF-8\" />
	<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
	<title>Dispatcher Configurator</title>
	<style>
		body { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #020617; color: #e5e7eb; }
		header { padding: 1rem 2rem; background: #020617; border-bottom: 1px solid #1f2937; display: flex; justify-content: space-between; align-items: center; }
		h1 { font-size: 1.3rem; margin: 0; }
		main { padding: 1.5rem 2rem; }
		.grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.8fr); gap: 1.25rem; align-items: flex-start; }
		.card { background: #020617; border-radius: 0.75rem; padding: 1rem 1.25rem; border: 1px solid #1e293b; box-shadow: 0 10px 20px rgba(15,23,42,0.6); }
		.card h2 { font-size: 1rem; margin: 0 0 0.5rem 0; }
		.muted { color: #9ca3af; font-size: 0.8rem; }
		.status { margin-top: 0.5rem; font-size: 0.8rem; }
		label { display: block; font-size: 0.8rem; margin-top: 0.5rem; margin-bottom: 0.15rem; color: #9ca3af; }
		input, select { width: 100%; border-radius: 0.5rem; border: 1px solid #1f2937; padding: 0.4rem 0.55rem; background: #020617; color: #e5e7eb; font-size: 0.85rem; }
		input:focus, select:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 1px #1d4ed8; }
		button { border-radius: 999px; padding: 0.4rem 0.9rem; border: none; font-size: 0.8rem; cursor: pointer; background: linear-gradient(to right, #059669, #22c55e); color: white; box-shadow: 0 8px 16px rgba(16,185,129,0.4); margin-right: 0.5rem; }
		button.secondary { background: #111827; box-shadow: none; }
		button.danger { background: #b91c1c; box-shadow: none; }
		button:disabled { opacity: 0.6; cursor: default; box-shadow: none; }
		.row { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 0.5rem; align-items: center; margin-top: 0.5rem; }
		.row-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
		.row-actions { display: flex; justify-content: flex-end; margin-top: 0.5rem; }
		.badge { border-radius: 999px; padding: 0.15rem 0.5rem; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.03em; background: #1e293b; color: #e5e7eb; }
		.monospace { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; font-size: 0.8rem; }
		a { color: #60a5fa; text-decoration: none; }
		a:hover { text-decoration: underline; }
	</style>
</head>
<body>
	<header>
		<div>
			<h1>Dispatcher Configurator</h1>
			<div class=\"muted\">Edit weights, nodes, and *arr instances without restarting.</div>
		</div>
		<div style=\"display:flex; align-items:center; gap:0.75rem;\">
			<nav class=\"small\" style=\"display:flex; gap:0.5rem; align-items:center;\">
				<a href=\"/\" style=\"color:#9ca3af; text-decoration:none;\">Dashboard</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/config\" style=\"color:#9ca3af; text-decoration:none;\">Config</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/nodes\" style=\"color:#9ca3af; text-decoration:none;\">/nodes</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/arr\" style=\"color:#9ca3af; text-decoration:none;\">/arr</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/health\" style=\"color:#9ca3af; text-decoration:none;\">/health</a>
			</nav>
		</div>
	</header>
	<main>
		<div class=\"grid\">
			<section class=\"card\">
				<h2>Dispatcher settings</h2>
				<div class=\"muted\">Control how nodes are scored and when they are excluded.</div>
				<label for=\"disk_weight\">Disk weight</label>
				<input id=\"disk_weight\" type=\"number\" step=\"0.1\" />
				<label for=\"download_weight\">Active downloads weight</label>
				<input id=\"download_weight\" type=\"number\" step=\"0.1\" />
				<label for=\"bandwidth_weight\">Bandwidth weight</label>
				<input id=\"bandwidth_weight\" type=\"number\" step=\"0.01\" />
				<label for=\"max_downloads\">Max active downloads per node</label>
				<input id=\"max_downloads\" type=\"number\" min=\"0\" />
				<label for=\"min_score\">Minimum allowed score</label>
				<input id=\"min_score\" type=\"number\" step=\"0.1\" />
				<label for=\"max_retries\">Submission retries</label>
				<input id=\"max_retries\" type=\"number\" min=\"1\" />
				<label for=\"save_path\">Override save path (optional)</label>
				<input id=\"save_path\" type=\"text\" placeholder=\"/downloads\" />
			</section>

			<section class=\"card\">
				<h2>qBittorrent nodes</h2>
				<div class=\"muted\">
					Each row describes one qBittorrent WebUI instance the dispatcher can send torrents to.
					<br />
					<span class=\"monospace\">Name</span> is a friendly label used in logs and the UI;
					<span class=\"monospace\">URL</span> is the WebUI base such as <span class=\"monospace\">http://qbittorrent:8080</span>;
					<span class=\"monospace\">Min free (GiB)</span> excludes a node once free space drops below that value.
				</div>
				<div id=\"nodes-container\"></div>
				<div class=\"row-actions\">
					<button type=\"button\" class=\"secondary\" id=\"add-node\">Add node</button>
				</div>
				<hr style=\"margin:0.9rem 0;border-color:#1f2937;border-width:0;border-top-width:1px;\" />
				<h2>Sonarr/Radarr</h2>
				<div class=\"muted\">
					(Optional) Sonarr/Radarr instances to monitor via <span class=\"monospace\">/arr</span>.
					<br />
					<span class=\"monospace\">API base URL</span> should point at the v3 API root such as
					<span class=\"monospace\">http://sonarr:8989/api/v3</span> or <span class=\"monospace\">http://radarr:7878/api/v3</span>.
				</div>
				<div id=\"arr-container\"></div>
				<div class=\"row-actions\">
					<button type=\"button\" class=\"secondary\" id=\"add-arr\">Add *arr</button>
				</div>
			</section>
		</div>
		<section class=\"card\" style=\"max-width:960px; margin:1.25rem auto 0;\">
			<div class=\"muted\">Changes are validated server-side and applied in-memory and on disk.</div>
			<div style=\"margin-top:0.75rem;\">
				<button id=\"save\">Save & apply</button>
				<button id=\"reload\" class=\"secondary\">Reload current config</button>
			</div>
			<div id=\"status\" class=\"status muted\"></div>
		</section>
	</main>
	<script>
		const statusEl = document.getElementById('status');
		const nodesContainer = document.getElementById('nodes-container');
		const arrContainer = document.getElementById('arr-container');
		const addNodeBtn = document.getElementById('add-node');
		const addArrBtn = document.getElementById('add-arr');
		const saveBtn = document.getElementById('save');
		const reloadBtn = document.getElementById('reload');

		function setStatus(text, isError = false) {
			statusEl.textContent = text;
			statusEl.style.color = isError ? '#fecaca' : '#9ca3af';
		}

		function createNodeRow(node) {
			const row = document.createElement('div');
			row.className = 'row';
			row.innerHTML = `
				<div>
					<label class="muted">Name</label>
					<input class="node-name" type="text" placeholder="qbittorrent-1" value="${node?.name || ''}">
				</div>
				<div>
					<label class="muted">URL</label>
					<input class="node-url" type="text" placeholder="http://qb:8080" value="${node?.url || ''}">
				</div>
				<div>
					<label class="muted">Username</label>
					<input class="node-username" type="text" value="${node?.username || ''}">
				</div>
				<div>
					<label class="muted">Password</label>
					<input class="node-password" type="password" value="${node?.password || ''}">
				</div>
				<div>
					<label class="muted">Min free (GiB)</label>
					<input class="node-minfree" type="number" step="1" min="0" value="${node?.min_free_gb ?? 0}">
					<button type="button" class="danger" style="margin-top:0.3rem; padding-inline:0.6rem; font-size:0.7rem;">Remove</button>
				</div>
			`;
			row.querySelector('.danger').addEventListener('click', () => row.remove());
			return row;
		}

		function createArrRow(inst) {
			const row = document.createElement('div');
			row.className = 'row row-4';
			row.innerHTML = `
				<div>
					<label class="muted">Name</label>
					<input class="arr-name" type="text" placeholder="sonarr-main" value="${inst?.name || ''}">
				</div>
				<div>
					<label class="muted">Type</label>
					<select class="arr-type">
						<option value="sonarr" ${inst?.type === 'sonarr' ? 'selected' : ''}>Sonarr</option>
						<option value="radarr" ${inst?.type === 'radarr' ? 'selected' : ''}>Radarr</option>
					</select>
				</div>
				<div>
					<label class="muted">API base URL</label>
					<input class="arr-url" type="text" placeholder="http://sonarr:8989/api/v3" value="${inst?.url || ''}">
				</div>
				<div>
					<label class="muted">API key</label>
					<input class="arr-key" type="password" value="${inst?.api_key || ''}">
					<button type="button" class="danger" style="margin-top:0.3rem; padding-inline:0.6rem; font-size:0.7rem;">Remove</button>
				</div>
			`;
			row.querySelector('.danger').addEventListener('click', () => row.remove());
			return row;
		}

		function buildPayloadFromForm() {
			const dispatcher = {
				 disk_weight: parseFloat(document.getElementById('disk_weight').value || '1') || 1,
				 download_weight: parseFloat(document.getElementById('download_weight').value || '2') || 2,
				 bandwidth_weight: parseFloat(document.getElementById('bandwidth_weight').value || '0.1') || 0.1,
				 max_downloads: parseInt(document.getElementById('max_downloads').value || '50', 10),
				 min_score: parseFloat(document.getElementById('min_score').value || '-1') || -1,
				 submission: {
					 max_retries: parseInt(document.getElementById('max_retries').value || '2', 10),
					 save_path: document.getElementById('save_path').value || null,
				 },
			};

			const nodes = [];
			document.querySelectorAll('.row .node-name').forEach((nameInput) => {
				const row = nameInput.closest('.row');
				const name = nameInput.value.trim();
				const url = row.querySelector('.node-url').value.trim();
				const username = row.querySelector('.node-username').value.trim();
				const password = row.querySelector('.node-password').value;
				const minFreeVal = parseFloat(row.querySelector('.node-minfree').value || '0');
				const min_free_gb = Number.isNaN(minFreeVal) ? 0 : minFreeVal;
				if (!name || !url) {
					return;
				}
				nodes.push({ name, url, username, password, min_free_gb });
			});

			const arr_instances = [];
			document.querySelectorAll('.row .arr-name').forEach((nameInput) => {
				const row = nameInput.closest('.row');
				const name = nameInput.value.trim();
				const url = row.querySelector('.arr-url').value.trim();
				const api_key = row.querySelector('.arr-key').value;
				const type = row.querySelector('.arr-type').value || 'sonarr';
				if (!name || !url || !api_key) {
					return;
				}
				arr_instances.push({ name, type, url, api_key });
			});

			return { dispatcher, nodes, arr_instances };
		}

		async function loadConfigJson() {
			setStatus('Loading current configuration...');
			try {
				const res = await fetch('/config/json');
				if (!res.ok) throw new Error('HTTP ' + res.status);
				const cfg = await res.json();

				document.getElementById('disk_weight').value = cfg.dispatcher.disk_weight;
				document.getElementById('download_weight').value = cfg.dispatcher.download_weight;
				document.getElementById('bandwidth_weight').value = cfg.dispatcher.bandwidth_weight;
				document.getElementById('max_downloads').value = cfg.dispatcher.max_downloads;
				document.getElementById('min_score').value = cfg.dispatcher.min_score;
				document.getElementById('max_retries').value = cfg.dispatcher.submission.max_retries;
				document.getElementById('save_path').value = cfg.dispatcher.submission.save_path || '';

				nodesContainer.innerHTML = '';
				(cfg.nodes || []).forEach((n) => {
					nodesContainer.appendChild(createNodeRow(n));
				});
				if (!cfg.nodes || cfg.nodes.length === 0) {
					nodesContainer.appendChild(createNodeRow({}));
				}

				arrContainer.innerHTML = '';
				(cfg.arr_instances || []).forEach((a) => {
					arrContainer.appendChild(createArrRow(a));
				});
				setStatus('Loaded current configuration');
			} catch (err) {
				console.error(err);
				setStatus('Failed to load configuration: ' + err, true);
			}
		}

		async function saveConfigJson() {
			saveBtn.disabled = true;
			setStatus('Validating and saving...');
			try {
				const payload = buildPayloadFromForm();
				const res = await fetch('/config/json', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(payload),
				});
				if (!res.ok) {
					const text = await res.text();
					setStatus('Error: ' + res.status + ' ' + text, true);
				} else {
					setStatus('Config applied successfully. Dispatcher reloaded.');
				}
			} catch (err) {
				console.error(err);
				setStatus('Request failed: ' + err, true);
			} finally {
				saveBtn.disabled = false;
			}
		}

		addNodeBtn.addEventListener('click', () => {
			nodesContainer.appendChild(createNodeRow({}));
		});
		addArrBtn.addEventListener('click', () => {
			arrContainer.appendChild(createArrRow({ type: 'sonarr' }));
		});
		saveBtn.addEventListener('click', saveConfigJson);
		reloadBtn.addEventListener('click', loadConfigJson);
		loadConfigJson();
	</script>
</body>
</html>"""

	return app


app = create_app()

