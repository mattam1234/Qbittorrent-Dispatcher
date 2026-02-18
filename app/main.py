from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Form, Response, Request, Depends
from fastapi.responses import PlainTextResponse, HTMLResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import (
	load_config,
	AppConfig,
	DEFAULT_CONFIG_PATH,
	parse_config,
	ArrInstanceConfig,
	NodeConfig as NodeConfigDC,
)
from .dispatcher import Dispatcher
from .models import (
	SubmitRequest,
	SubmitDecision,
	NodeStatus,
	NodeMetrics,
	DecisionDebug,
	ConfigRaw,
	ArrStatus,
	DecisionRecord,
	AppConfigModel,
	DispatcherConfig,
	SubmissionConfig,
	NodeConfigModel,
	ArrInstanceModel,
	MessagingServiceModel,
	N8nConfigModel,
	OverseerrConfigModel,
	JellyseerrConfigModel,
	ProwlarrConfigModel,
	IntegrationsConfigModel,
	RequestTrackingModel,
)
from .arr_client import check_arr_instance
from .qb_client import QbittorrentNodeClient
from .metrics import update_arr_metrics
from .integrations import OverseerrClient, JellyseerrClient, ProwlarrClient
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

	async def require_admin(request: Request) -> None:
		"""Optional admin API key check for management endpoints.

		If dispatcher.admin_api_key is set, require header X-API-Key to match it.
		"""

		key = getattr(config_obj.dispatcher, "admin_api_key", None)
		if not key:
			return
		req_key = request.headers.get("x-api-key")
		if req_key != key:
			raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")

	@app.post("/submit", response_model=SubmitDecision)
	async def submit(req: SubmitRequest, _: None = Depends(require_admin)) -> SubmitDecision:  # noqa: D401
		"""Submit a new download and have the dispatcher pick the best node."""

		decision = await dispatcher.submit(req)

		if decision.status == "rejected":
			raise HTTPException(status_code=503, detail=decision.model_dump())

		if decision.status == "failed":
			raise HTTPException(status_code=503, detail=decision.model_dump())

		return decision

	@app.get("/config/raw", response_class=PlainTextResponse)
	async def get_config_raw(_: None = Depends(require_admin)) -> str:
		"""Return the current YAML configuration file."""

		try:
			return DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
		except FileNotFoundError as exc:  # noqa: PERF203
			raise HTTPException(status_code=404, detail="config.yaml not found") from exc

	@app.post("/config/raw")
	async def update_config_raw(payload: ConfigRaw, _: None = Depends(require_admin)) -> dict[str, str]:
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
	async def get_config_json(_: None = Depends(require_admin)) -> AppConfigModel:
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
		
		# Build integrations config
		integrations = getattr(config_obj, "integrations", None)
		integrations_cfg = IntegrationsConfigModel()
		if integrations:
			integrations_cfg = IntegrationsConfigModel(
				n8n=N8nConfigModel(
					enabled=integrations.n8n.enabled,
					webhook_url=integrations.n8n.webhook_url,
					api_key=integrations.n8n.api_key,
				),
				messaging_services=[
					MessagingServiceModel(
						name=svc.name,
						type=svc.type,
						webhook_url=svc.webhook_url,
						bot_token=svc.bot_token,
						chat_id=svc.chat_id,
						enabled=svc.enabled,
					)
					for svc in integrations.messaging_services
				],
				overseerr=OverseerrConfigModel(
					enabled=integrations.overseerr.enabled,
					url=integrations.overseerr.url,
					api_key=integrations.overseerr.api_key,
				),
				jellyseerr=JellyseerrConfigModel(
					enabled=integrations.jellyseerr.enabled,
					url=integrations.jellyseerr.url,
					api_key=integrations.jellyseerr.api_key,
				),
				prowlarr=ProwlarrConfigModel(
					enabled=integrations.prowlarr.enabled,
					url=integrations.prowlarr.url,
					api_key=integrations.prowlarr.api_key,
				),
			)
		
		# Build request tracking config
		tracking = getattr(config_obj, "request_tracking", None)
		tracking_cfg = RequestTrackingModel()
		if tracking:
			tracking_cfg = RequestTrackingModel(
				enabled=tracking.enabled,
				check_duplicates=tracking.check_duplicates,
				check_quality_profiles=tracking.check_quality_profiles,
				send_suggestions=tracking.send_suggestions,
			)

		return AppConfigModel(
			dispatcher=dispatcher_cfg,
			nodes=nodes_cfg,
			arr_instances=arr_cfg,
			integrations=integrations_cfg,
			request_tracking=tracking_cfg,
		)

	@app.post("/config/json", response_model=AppConfigModel)
	async def update_config_json(payload: AppConfigModel, _: None = Depends(require_admin)) -> AppConfigModel:
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
		.integration-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; margin-top: 0.75rem; }
		.integration-item { background: #111827; border-radius: 0.5rem; padding: 0.75rem; border: 1px solid #1f2937; }
		.integration-item h3 { font-size: 0.85rem; margin: 0 0 0.25rem 0; }
		.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; margin-top: 0.5rem; }
		.stat-box { background: #111827; border-radius: 0.5rem; padding: 0.5rem; text-align: center; border: 1px solid #1f2937; }
		.stat-box .label { font-size: 0.7rem; color: #9ca3af; }
		.stat-box .value { font-size: 1.1rem; font-weight: 600; margin-top: 0.15rem; }
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
				<a href=\"/decisions\" style=\"color:#9ca3af; text-decoration:none;\">Decisions</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/metrics\" style=\"color:#9ca3af; text-decoration:none;\">Metrics</a>
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
		
		<div class="layout" style="margin-top:1.5rem;">
			<section class="card">
				<h2>Integrations Status</h2>
				<div class="muted">Status of n8n, Overseerr, Jellyseerr, Prowlarr, and messaging services.</div>
				<div class="integration-grid" id="integrations-grid">
					<div class="integration-item">
						<h3>Loading...</h3>
						<div class="muted small">Checking status...</div>
					</div>
				</div>
				<div class="muted small" style="margin-top:0.75rem;">Auto-refreshes every 15 seconds.</div>
			</section>
			
			<section class="card">
				<h2>Request Tracking</h2>
				<div class="muted">Overview of tracked download requests.</div>
				<div class="stat-grid" id="tracking-stats">
					<div class="stat-box">
						<div class="label">Total</div>
						<div class="value">-</div>
					</div>
					<div class="stat-box">
						<div class="label">Active</div>
						<div class="value">-</div>
					</div>
					<div class="stat-box">
						<div class="label">Completed</div>
						<div class="value">-</div>
					</div>
				</div>
				<div class="muted small" style="margin-top:0.75rem;" id="tracking-status">Request tracking not enabled</div>
			</section>
		</div>
		
		<section class="card" style="margin-top:1.5rem;">
			<h2>Recent decisions</h2>
			<div class="muted small">Most recent routing outcomes (newest last).</div>
			<table>
				<thead>
					<tr>
						<th>Time</th>
						<th>Request</th>
						<th>Category</th>
						<th>Size (GiB)</th>
						<th>Status</th>
						<th>Selected node</th>
					</tr>
				</thead>
				<tbody id="decisions-body"></tbody>
			</table>
			<div class="muted small" style="margin-top:0.5rem;">Shows up to the 50 most recent submissions.</div>
		</section>
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

		async function fetchDecisions() {
			const body = document.getElementById('decisions-body');
			if (!body) return;
			try {
				const res = await fetch('/decisions?limit=50');
				if (!res.ok) throw new Error('HTTP ' + res.status);
				const data = await res.json();
				body.innerHTML = '';
				for (const rec of data) {
					const tr = document.createElement('tr');
					const d = new Date(rec.timestamp * 1000);
					const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
					const size = (rec.size_estimate_gb ?? 0).toFixed ? rec.size_estimate_gb.toFixed(1) : rec.size_estimate_gb;
					tr.innerHTML = `
						<td class="small">${timeStr}</td>
						<td class="small monospace">${rec.request_name}</td>
						<td class="small">${rec.request_category}</td>
						<td class="small">${size}</td>
						<td class="small">${rec.status}</td>
						<td class="small monospace">${rec.selected_node || '—'}</td>
					`;
					body.appendChild(tr);
				}
			} catch (err) {
				console.error(err);
			}
		}

		async function fetchIntegrations() {
			const grid = document.getElementById('integrations-grid');
			if (!grid) return;
			try {
				const res = await fetch('/integrations/status');
				if (!res.ok) throw new Error('HTTP ' + res.status);
				const data = await res.json();
				grid.innerHTML = '';
				
				// n8n
				const n8nItem = document.createElement('div');
				n8nItem.className = 'integration-item';
				const n8nBadge = data.n8n.enabled ? (data.n8n.connected ? 'badge badge-ok' : 'badge badge-bad') : 'badge badge-warn';
				const n8nStatus = data.n8n.enabled ? (data.n8n.connected ? 'connected' : 'disconnected') : 'disabled';
				n8nItem.innerHTML = `
					<h3>n8n</h3>
					<span class="${n8nBadge}">${n8nStatus}</span>
					${data.n8n.error ? `<div class="muted small" style="margin-top:0.25rem;">${data.n8n.error}</div>` : ''}
				`;
				grid.appendChild(n8nItem);
				
				// Overseerr
				const overseerrItem = document.createElement('div');
				overseerrItem.className = 'integration-item';
				const overseerrBadge = data.overseerr.enabled ? (data.overseerr.connected ? 'badge badge-ok' : 'badge badge-bad') : 'badge badge-warn';
				const overseerrStatus = data.overseerr.enabled ? (data.overseerr.connected ? 'connected' : 'disconnected') : 'disabled';
				overseerrItem.innerHTML = `
					<h3>Overseerr</h3>
					<span class="${overseerrBadge}">${overseerrStatus}</span>
					${data.overseerr.version ? `<div class="muted small" style="margin-top:0.25rem;">v${data.overseerr.version}</div>` : ''}
					${data.overseerr.error ? `<div class="muted small" style="margin-top:0.25rem;">${data.overseerr.error}</div>` : ''}
				`;
				grid.appendChild(overseerrItem);
				
				// Jellyseerr
				const jellyseerrItem = document.createElement('div');
				jellyseerrItem.className = 'integration-item';
				const jellyseerrBadge = data.jellyseerr.enabled ? (data.jellyseerr.connected ? 'badge badge-ok' : 'badge badge-bad') : 'badge badge-warn';
				const jellyseerrStatus = data.jellyseerr.enabled ? (data.jellyseerr.connected ? 'connected' : 'disconnected') : 'disabled';
				jellyseerrItem.innerHTML = `
					<h3>Jellyseerr</h3>
					<span class="${jellyseerrBadge}">${jellyseerrStatus}</span>
					${data.jellyseerr.version ? `<div class="muted small" style="margin-top:0.25rem;">v${data.jellyseerr.version}</div>` : ''}
					${data.jellyseerr.error ? `<div class="muted small" style="margin-top:0.25rem;">${data.jellyseerr.error}</div>` : ''}
				`;
				grid.appendChild(jellyseerrItem);
				
				// Prowlarr
				const prowlarrItem = document.createElement('div');
				prowlarrItem.className = 'integration-item';
				const prowlarrBadge = data.prowlarr.enabled ? (data.prowlarr.connected ? 'badge badge-ok' : 'badge badge-bad') : 'badge badge-warn';
				const prowlarrStatus = data.prowlarr.enabled ? (data.prowlarr.connected ? 'connected' : 'disconnected') : 'disabled';
				prowlarrItem.innerHTML = `
					<h3>Prowlarr</h3>
					<span class="${prowlarrBadge}">${prowlarrStatus}</span>
					${data.prowlarr.version ? `<div class="muted small" style="margin-top:0.25rem;">v${data.prowlarr.version}</div>` : ''}
					${data.prowlarr.error ? `<div class="muted small" style="margin-top:0.25rem;">${data.prowlarr.error}</div>` : ''}
				`;
				grid.appendChild(prowlarrItem);
				
				// Messaging Services
				if (data.messaging_services && data.messaging_services.length > 0) {
					for (const svc of data.messaging_services) {
						const svcItem = document.createElement('div');
						svcItem.className = 'integration-item';
						const svcBadge = svc.enabled ? 'badge badge-ok' : 'badge badge-warn';
						const svcStatus = svc.enabled ? 'enabled' : 'disabled';
						svcItem.innerHTML = `
							<h3>${svc.name}</h3>
							<span class="${svcBadge}">${svcStatus}</span>
							<div class="muted small" style="margin-top:0.25rem;">${svc.type}</div>
						`;
						grid.appendChild(svcItem);
					}
				}
			} catch (err) {
				console.error(err);
				grid.innerHTML = '<div class="muted small">Error loading integrations status</div>';
			}
		}

		async function fetchRequestTracking() {
			const statsGrid = document.getElementById('tracking-stats');
			const statusEl = document.getElementById('tracking-status');
			if (!statsGrid || !statusEl) return;
			try {
				const res = await fetch('/request-tracking/all');
				if (!res.ok) {
					if (res.status === 404 || res.status === 400) {
						const data = await res.json();
						if (data.error) {
							statusEl.textContent = data.error;
							return;
						}
					}
					throw new Error('HTTP ' + res.status);
				}
				const data = await res.json();
				
				// Count statuses
				let activeCount = 0;
				let completedCount = 0;
				for (const req of data.requests || []) {
					if (req.status === 'downloading' || req.status === 'pending') {
						activeCount++;
					} else if (req.status === 'completed') {
						completedCount++;
					}
				}
				
				const statBoxes = statsGrid.querySelectorAll('.stat-box .value');
				if (statBoxes.length >= 3) {
					statBoxes[0].textContent = data.count || 0;
					statBoxes[1].textContent = activeCount;
					statBoxes[2].textContent = completedCount;
				}
				
				statusEl.textContent = `Tracking ${data.count || 0} requests`;
			} catch (err) {
				console.error(err);
				statusEl.textContent = 'Error loading request tracking';
			}
		}

		document.getElementById('debug-form').addEventListener('submit', runDecision);
		fetchNodes();
		fetchArr();
		fetchDecisions();
		fetchIntegrations();
		fetchRequestTracking();
		setInterval(fetchNodes, 5000);
		setInterval(fetchArr, 10000);
		setInterval(fetchDecisions, 15000);
		setInterval(fetchIntegrations, 15000);
		setInterval(fetchRequestTracking, 15000);
	</script>
</body>
</html>"""

	@app.get("/nodes", response_model=list[NodeStatus])
	async def list_nodes(_: None = Depends(require_admin)) -> list[NodeStatus]:
		"""Return current node metrics, scores, and exclusion flags."""

		return await dispatcher.get_node_statuses()

	# --- qBittorrent-compatible endpoints for Sonarr/Radarr ---

	@app.post("/api/v2/auth/login", response_class=PlainTextResponse)
	async def qb_login(
		response: Response,
		username: str = Form(""),  # noqa: ARG001
		password: str = Form(""),  # noqa: ARG001
	) -> str:
		"""Fake qBittorrent login; accepts any credentials.

		Sonarr/Radarr expect this endpoint to exist when configured
		as a qBittorrent download client. We don't enforce auth but
		return "Ok." and a dummy SID cookie.
		"""

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
	async def debug_decision(req: SubmitRequest, _: None = Depends(require_admin)) -> DecisionDebug:
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
	async def arr_status(_: None = Depends(require_admin)) -> list[ArrStatus]:
		"""Return connectivity status for configured Sonarr/Radarr instances."""

		instances = getattr(config_obj, "arr_instances", []) or []
		if not instances:
			return []

		results = await asyncio.gather(*(check_arr_instance(inst) for inst in instances))
		out: list[ArrStatus] = []
		for inst, state in zip(instances, results, strict=False):
			update_arr_metrics(inst.name, inst.type, state.reachable)
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

	@app.get("/integrations/status")
	async def integrations_status(_: None = Depends(require_admin)) -> dict:
		"""Return status of all configured integrations."""
		
		status = {
			"n8n": {
				"enabled": config_obj.integrations.n8n.enabled,
				"connected": False,
				"error": None,
			},
			"overseerr": {
				"enabled": config_obj.integrations.overseerr.enabled,
				"connected": False,
				"version": None,
				"error": None,
			},
			"jellyseerr": {
				"enabled": config_obj.integrations.jellyseerr.enabled,
				"connected": False,
				"version": None,
				"error": None,
			},
			"prowlarr": {
				"enabled": config_obj.integrations.prowlarr.enabled,
				"connected": False,
				"version": None,
				"error": None,
			},
			"messaging_services": [],
		}
		
		# Check n8n connection
		if config_obj.integrations.n8n.enabled:
			connected, error = await dispatcher.n8n_client.check_connection()
			status["n8n"]["connected"] = connected
			status["n8n"]["error"] = error
		
		# Check Overseerr
		if config_obj.integrations.overseerr.enabled:
			client = OverseerrClient(config_obj.integrations.overseerr)
			connected, result = await client.check_status()
			status["overseerr"]["connected"] = connected
			if connected:
				status["overseerr"]["version"] = result
			else:
				status["overseerr"]["error"] = result
		
		# Check Jellyseerr
		if config_obj.integrations.jellyseerr.enabled:
			client = JellyseerrClient(config_obj.integrations.jellyseerr)
			connected, result = await client.check_status()
			status["jellyseerr"]["connected"] = connected
			if connected:
				status["jellyseerr"]["version"] = result
			else:
				status["jellyseerr"]["error"] = result
		
		# Check Prowlarr
		if config_obj.integrations.prowlarr.enabled:
			client = ProwlarrClient(config_obj.integrations.prowlarr)
			connected, result = await client.check_status()
			status["prowlarr"]["connected"] = connected
			if connected:
				status["prowlarr"]["version"] = result
			else:
				status["prowlarr"]["error"] = result
		
		# List messaging services
		for svc in config_obj.integrations.messaging_services:
			status["messaging_services"].append({
				"name": svc.name,
				"type": svc.type,
				"enabled": svc.enabled,
			})
		
		return status

	@app.get("/integrations/overseerr/requests")
	async def overseerr_requests(_: None = Depends(require_admin)) -> dict:
		"""Get pending requests from Overseerr."""
		
		if not config_obj.integrations.overseerr.enabled:
			return {"error": "Overseerr not enabled", "requests": []}
		
		client = OverseerrClient(config_obj.integrations.overseerr)
		requests = await client.get_pending_requests()
		
		return {
			"count": len(requests),
			"requests": [
				{
					"id": req.id,
					"title": req.title,
					"type": req.media_type,
					"year": req.year,
					"status": req.status,
					"requested_by": req.requested_by,
				}
				for req in requests
			],
		}

	@app.get("/integrations/jellyseerr/requests")
	async def jellyseerr_requests(_: None = Depends(require_admin)) -> dict:
		"""Get pending requests from Jellyseerr."""
		
		if not config_obj.integrations.jellyseerr.enabled:
			return {"error": "Jellyseerr not enabled", "requests": []}
		
		client = JellyseerrClient(config_obj.integrations.jellyseerr)
		requests = await client.get_pending_requests()
		
		return {
			"count": len(requests),
			"requests": [
				{
					"id": req.id,
					"title": req.title,
					"type": req.media_type,
					"year": req.year,
					"status": req.status,
					"requested_by": req.requested_by,
				}
				for req in requests
			],
		}

	@app.get("/integrations/prowlarr/indexers")
	async def prowlarr_indexers(_: None = Depends(require_admin)) -> dict:
		"""Get configured indexers from Prowlarr."""
		
		if not config_obj.integrations.prowlarr.enabled:
			return {"error": "Prowlarr not enabled", "indexers": []}
		
		client = ProwlarrClient(config_obj.integrations.prowlarr)
		indexers = await client.get_indexers()
		
		return {
			"count": len(indexers),
			"indexers": indexers,
		}

	@app.get("/request-tracking/all")
	async def get_all_tracked_requests(_: None = Depends(require_admin)) -> dict:
		"""Get all tracked requests."""
		
		if not dispatcher.request_tracker:
			return {"error": "Request tracking not enabled", "requests": []}
		
		requests = dispatcher.request_tracker.get_all_requests()
		
		return {
			"count": len(requests),
			"requests": [
				{
					"name": req.name,
					"category": req.category,
					"size_gb": req.size_estimate_gb,
					"timestamp": req.timestamp.isoformat(),
					"source": req.source,
					"quality_profile": req.quality_profile,
					"selected_node": req.selected_node,
					"status": req.status,
				}
				for req in requests
			],
		}

	@app.get("/request-tracking/category/{category}")
	async def get_tracked_requests_by_category(category: str, _: None = Depends(require_admin)) -> dict:
		"""Get tracked requests for a specific category."""
		
		if not dispatcher.request_tracker:
			return {"error": "Request tracking not enabled", "requests": []}
		
		requests = dispatcher.request_tracker.get_requests_by_category(category)
		
		return {
			"category": category,
			"count": len(requests),
			"requests": [
				{
					"name": req.name,
					"category": req.category,
					"size_gb": req.size_estimate_gb,
					"timestamp": req.timestamp.isoformat(),
					"source": req.source,
					"quality_profile": req.quality_profile,
					"selected_node": req.selected_node,
					"status": req.status,
				}
				for req in requests
			],
		}

	@app.get("/quality-profiles")
	async def get_quality_profiles(_: None = Depends(require_admin)) -> dict:
		"""Get quality profiles from all configured ARR instances."""
		
		profiles = await dispatcher.quality_checker.get_all_profiles()
		
		result = {}
		for arr_name, arr_profiles in profiles.items():
			result[arr_name] = [
				{
					"id": p.id,
					"name": p.name,
					"cutoff": p.cutoff,
					"upgrade_allowed": p.upgrade_allowed,
				}
				for p in arr_profiles
			]
		
		return result

	@app.get("/metrics")
	async def metrics_endpoint() -> Response:
		"""Expose Prometheus metrics for scraping."""

		data = generate_latest()
		return Response(content=data, media_type=CONTENT_TYPE_LATEST)

	@app.get("/decisions", response_model=list[DecisionRecord])
	async def list_decisions(limit: int = 50, _: None = Depends(require_admin)) -> list[DecisionRecord]:
		"""Return recent routing decisions from the in-memory history buffer."""

		try:
			limit = int(limit)
		except Exception:  # noqa: BLE001
			limit = 50
		return dispatcher.get_decisions(limit=limit)

		@app.post("/config/test/node", response_model=NodeStatus)
		async def test_node_connection(node: NodeConfigModel, _: None = Depends(require_admin)) -> NodeStatus:
			"""Test connectivity to a qBittorrent node using the provided settings.

			This does not persist any configuration; it simply attempts to reach the
			WebUI and returns basic metrics or an error string.
			"""

			config_dc = NodeConfigDC(
				name=node.name,
				url=node.url,
				username=node.username,
				password=node.password,
				min_free_gb=node.min_free_gb,
				weight=1.0,
			)

			client = QbittorrentNodeClient(config_dc)
			try:
				state = await to_thread.run_sync(client.fetch_state)
				metrics = NodeMetrics(
					name=config_dc.name,
					free_disk_gb=state.free_disk_gb,
					active_downloads=state.active_downloads,
					paused_downloads=state.paused_downloads,
					global_download_rate_mbps=state.global_download_rate_mbps,
					reachable=True,
					excluded_reason=None,
					score=None,
				)
				return NodeStatus(metrics=metrics, excluded=False)
			except Exception as exc:  # noqa: BLE001
				metrics = NodeMetrics(
					name=config_dc.name,
					free_disk_gb=None,
					active_downloads=0,
					paused_downloads=0,
					global_download_rate_mbps=0.0,
					reachable=False,
					excluded_reason=str(exc),
					score=None,
				)
				return NodeStatus(metrics=metrics, excluded=True)

		@app.post("/config/test/arr", response_model=ArrStatus)
		async def test_arr_connection(inst: ArrInstanceModel, _: None = Depends(require_admin)) -> ArrStatus:
			"""Test connectivity to a Sonarr/Radarr instance using the provided settings.

			This does not persist any configuration; it simply calls /system/status
			and reports reachability and version.
			"""

			config_dc = ArrInstanceConfig(
				name=inst.name,
				type=inst.type,
				url=inst.url,
				api_key=inst.api_key,
			)

			state = await check_arr_instance(config_dc)
			return ArrStatus(
				name=config_dc.name,
				type=config_dc.type,
				url=config_dc.url,
				reachable=state.reachable,
				version=state.version,
				error=state.error,
			)

	@app.get("/config", response_class=HTMLResponse)
	async def config_ui(_: None = Depends(require_admin)) -> str:
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
				<a href=\"/decisions\" style=\"color:#9ca3af; text-decoration:none;\">Decisions</a>
				<span style=\"color:#4b5563;\">·</span>
				<a href=\"/metrics\" style=\"color:#9ca3af; text-decoration:none;\">Metrics</a>
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
				<hr style=\"margin:0.9rem 0;border-color:#1f2937;border-width:0;border-top-width:1px;\" />
				<h2>Request Tracking</h2>
				<div class=\"muted\">
					Configure request tracking to prevent duplicates and check for quality upgrades.
				</div>
				<label for=\"tracking_enabled\">Enable request tracking</label>
				<select id=\"tracking_enabled\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"check_duplicates\">Check for duplicates</label>
				<select id=\"check_duplicates\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"check_quality_profiles\">Check quality profiles</label>
				<select id=\"check_quality_profiles\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"send_suggestions\">Send quality suggestions</label>
				<select id=\"send_suggestions\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
			</section>
		</div>
		<div class=\"grid\" style=\"margin-top:1.25rem;\">
			<section class=\"card\">
				<h2>Integration: n8n</h2>
				<div class=\"muted\">Configure n8n automation platform integration.</div>
				<label for=\"n8n_enabled\">Enable n8n</label>
				<select id=\"n8n_enabled\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"n8n_webhook_url\">Webhook URL</label>
				<input id=\"n8n_webhook_url\" type=\"text\" placeholder=\"http://n8n:5678/webhook/qbittorrent-dispatcher\" />
				<label for=\"n8n_api_key\">API Key (optional)</label>
				<input id=\"n8n_api_key\" type=\"password\" placeholder=\"Optional for webhook authentication\" />
			</section>
			
			<section class=\"card\">
				<h2>Integration: Overseerr</h2>
				<div class=\"muted\">Configure Overseerr for media request management.</div>
				<label for=\"overseerr_enabled\">Enable Overseerr</label>
				<select id=\"overseerr_enabled\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"overseerr_url\">URL</label>
				<input id=\"overseerr_url\" type=\"text\" placeholder=\"http://overseerr:5055\" />
				<label for=\"overseerr_api_key\">API Key</label>
				<input id=\"overseerr_api_key\" type=\"password\" placeholder=\"Your Overseerr API key\" />
			</section>
		</div>
		<div class=\"grid\" style=\"margin-top:1.25rem;\">
			<section class=\"card\">
				<h2>Integration: Jellyseerr</h2>
				<div class=\"muted\">Configure Jellyseerr for media request management.</div>
				<label for=\"jellyseerr_enabled\">Enable Jellyseerr</label>
				<select id=\"jellyseerr_enabled\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"jellyseerr_url\">URL</label>
				<input id=\"jellyseerr_url\" type=\"text\" placeholder=\"http://jellyseerr:5055\" />
				<label for=\"jellyseerr_api_key\">API Key</label>
				<input id=\"jellyseerr_api_key\" type=\"password\" placeholder=\"Your Jellyseerr API key\" />
			</section>
			
			<section class=\"card\">
				<h2>Integration: Prowlarr</h2>
				<div class=\"muted\">Configure Prowlarr for indexer management.</div>
				<label for=\"prowlarr_enabled\">Enable Prowlarr</label>
				<select id=\"prowlarr_enabled\">
					<option value=\"true\">Enabled</option>
					<option value=\"false\">Disabled</option>
				</select>
				<label for=\"prowlarr_url\">URL</label>
				<input id=\"prowlarr_url\" type=\"text\" placeholder=\"http://prowlarr:9696\" />
				<label for=\"prowlarr_api_key\">API Key</label>
				<input id=\"prowlarr_api_key\" type=\"password\" placeholder=\"Your Prowlarr API key\" />
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
					<div style="display:flex; gap:0.3rem; margin-top:0.3rem;">
						<button type="button" class="secondary node-test" style="padding-inline:0.6rem; font-size:0.7rem;">Test</button>
						<button type="button" class="danger node-remove" style="padding-inline:0.6rem; font-size:0.7rem;">Remove</button>
					</div>
					<div class="muted node-test-status" style="margin-top:0.2rem; font-size:0.72rem;"></div>
				</div>
			`;
			const removeBtn = row.querySelector('.node-remove');
			removeBtn.addEventListener('click', () => row.remove());
			const testBtn = row.querySelector('.node-test');
			const testStatus = row.querySelector('.node-test-status');
			testBtn.addEventListener('click', async () => {
				const nameInput = row.querySelector('.node-name');
				const urlInput = row.querySelector('.node-url');
				const usernameInput = row.querySelector('.node-username');
				const passwordInput = row.querySelector('.node-password');
				const minfreeInput = row.querySelector('.node-minfree');

				const name = nameInput.value.trim();
				const url = urlInput.value.trim();
				if (!name || !url) {
					testStatus.textContent = 'Name and URL are required to test.';
					return;
				}

				const minFreeVal = parseFloat(minfreeInput.value || '0');
				const min_free_gb = Number.isNaN(minFreeVal) ? 0 : minFreeVal;
				const payload = {
					name,
					url,
					username: usernameInput.value.trim(),
					password: passwordInput.value,
					min_free_gb,
				};

				testBtn.disabled = true;
				testStatus.textContent = 'Testing connection...';
				try {
					const res = await fetch('/config/test/node', {
						method: 'POST',
						headers: { 'Content-Type': 'application/json' },
						body: JSON.stringify(payload),
					});
					if (!res.ok) {
						testStatus.textContent = 'Error: ' + res.status + ' ' + (await res.text());
					} else {
						const data = await res.json();
						if (data.metrics.reachable) {
							const free = data.metrics.free_disk_gb != null ? data.metrics.free_disk_gb.toFixed(1) : 'n/a';
							const active = data.metrics.active_downloads;
							testStatus.textContent = `OK: free ${free} GiB, active ${active}`;
						} else {
							testStatus.textContent = 'Unreachable: ' + (data.metrics.excluded_reason || 'see logs');
						}
					}
				} catch (err) {
					console.error(err);
					testStatus.textContent = 'Request failed: ' + err;
				} finally {
					testBtn.disabled = false;
				}
			});
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
					<div style="display:flex; gap:0.3rem; margin-top:0.3rem;">
						<button type="button" class="secondary arr-test" style="padding-inline:0.6rem; font-size:0.7rem;">Test</button>
						<button type="button" class="danger arr-remove" style="padding-inline:0.6rem; font-size:0.7rem;">Remove</button>
					</div>
					<div class="muted arr-test-status" style="margin-top:0.2rem; font-size:0.72rem;"></div>
				</div>
			`;
			const removeBtn = row.querySelector('.arr-remove');
			removeBtn.addEventListener('click', () => row.remove());
			const testBtn = row.querySelector('.arr-test');
			const testStatus = row.querySelector('.arr-test-status');
			testBtn.addEventListener('click', async () => {
				const nameInput = row.querySelector('.arr-name');
				const urlInput = row.querySelector('.arr-url');
				const keyInput = row.querySelector('.arr-key');
				const typeSelect = row.querySelector('.arr-type');

				const name = nameInput.value.trim();
				const url = urlInput.value.trim();
				const api_key = keyInput.value;
				const type = typeSelect.value || 'sonarr';
				if (!name || !url || !api_key) {
					testStatus.textContent = 'Name, URL, and API key are required to test.';
					return;
				}

				const payload = { name, type, url, api_key };
				testBtn.disabled = true;
				testStatus.textContent = 'Testing connection...';
				try {
					const res = await fetch('/config/test/arr', {
						method: 'POST',
						headers: { 'Content-Type': 'application/json' },
						body: JSON.stringify(payload),
					});
					if (!res.ok) {
						testStatus.textContent = 'Error: ' + res.status + ' ' + (await res.text());
					} else {
						const data = await res.json();
						if (data.reachable) {
							const ver = data.version ? 'v' + data.version : '';
							testStatus.textContent = 'OK: reachable ' + ver;
						} else {
							testStatus.textContent = 'Unreachable: ' + (data.error || 'see logs');
						}
					}
				} catch (err) {
					console.error(err);
					testStatus.textContent = 'Request failed: ' + err;
				} finally {
					testBtn.disabled = false;
				}
			});
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

			const integrations = {
				n8n: {
					enabled: document.getElementById('n8n_enabled').value === 'true',
					webhook_url: document.getElementById('n8n_webhook_url').value || null,
					api_key: document.getElementById('n8n_api_key').value || null,
				},
				messaging_services: [],
				overseerr: {
					enabled: document.getElementById('overseerr_enabled').value === 'true',
					url: document.getElementById('overseerr_url').value || '',
					api_key: document.getElementById('overseerr_api_key').value || '',
				},
				jellyseerr: {
					enabled: document.getElementById('jellyseerr_enabled').value === 'true',
					url: document.getElementById('jellyseerr_url').value || '',
					api_key: document.getElementById('jellyseerr_api_key').value || '',
				},
				prowlarr: {
					enabled: document.getElementById('prowlarr_enabled').value === 'true',
					url: document.getElementById('prowlarr_url').value || '',
					api_key: document.getElementById('prowlarr_api_key').value || '',
				},
			};

			const request_tracking = {
				enabled: document.getElementById('tracking_enabled').value === 'true',
				check_duplicates: document.getElementById('check_duplicates').value === 'true',
				check_quality_profiles: document.getElementById('check_quality_profiles').value === 'true',
				send_suggestions: document.getElementById('send_suggestions').value === 'true',
			};

			return { dispatcher, nodes, arr_instances, integrations, request_tracking };
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
				
				// Load integrations config
				if (cfg.integrations) {
					document.getElementById('n8n_enabled').value = cfg.integrations.n8n.enabled ? 'true' : 'false';
					document.getElementById('n8n_webhook_url').value = cfg.integrations.n8n.webhook_url || '';
					document.getElementById('n8n_api_key').value = cfg.integrations.n8n.api_key || '';
					
					document.getElementById('overseerr_enabled').value = cfg.integrations.overseerr.enabled ? 'true' : 'false';
					document.getElementById('overseerr_url').value = cfg.integrations.overseerr.url || '';
					document.getElementById('overseerr_api_key').value = cfg.integrations.overseerr.api_key || '';
					
					document.getElementById('jellyseerr_enabled').value = cfg.integrations.jellyseerr.enabled ? 'true' : 'false';
					document.getElementById('jellyseerr_url').value = cfg.integrations.jellyseerr.url || '';
					document.getElementById('jellyseerr_api_key').value = cfg.integrations.jellyseerr.api_key || '';
					
					document.getElementById('prowlarr_enabled').value = cfg.integrations.prowlarr.enabled ? 'true' : 'false';
					document.getElementById('prowlarr_url').value = cfg.integrations.prowlarr.url || '';
					document.getElementById('prowlarr_api_key').value = cfg.integrations.prowlarr.api_key || '';
				}
				
				// Load request tracking config
				if (cfg.request_tracking) {
					document.getElementById('tracking_enabled').value = cfg.request_tracking.enabled ? 'true' : 'false';
					document.getElementById('check_duplicates').value = cfg.request_tracking.check_duplicates ? 'true' : 'false';
					document.getElementById('check_quality_profiles').value = cfg.request_tracking.check_quality_profiles ? 'true' : 'false';
					document.getElementById('send_suggestions').value = cfg.request_tracking.send_suggestions ? 'true' : 'false';
				}
				
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

