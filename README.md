# Space-Aware qBittorrent Download Dispatcher

FastAPI-based dispatcher that selects the best qBittorrent node based on free disk space, active downloads, and bandwidth, then forwards torrent submissions from *arr apps to the best qBittorrent node.

## Configuration

Edit [config.yaml](config.yaml) to define dispatcher weights, security, qBittorrent nodes, and optional *arr instances.

Top-level structure:

```yaml
dispatcher:
	disk_weight: 1.0
	download_weight: 2.0
	bandwidth_weight: 0.1
	max_downloads: 50
	min_score: -1.0
	# Optional admin API key; when set, protects management endpoints with X-API-Key
	# admin_api_key: supersecret
	submission:
		max_retries: 2
		save_path: null

nodes:
	- name: qbittorrent-1
		url: http://qbittorrent:8080
		username: admin
		password: secret
		min_free_gb: 500
		# Optional per-node weight multiplier (default 1.0)
		# weight: 1.0

arr_instances:
	- name: sonarr-main
		type: sonarr
		url: http://sonarr:8989/api/v3
		api_key: YOUR_SONARR_API_KEY
	- name: radarr-main
		type: radarr
		url: http://radarr:7878/api/v3
		api_key: YOUR_RADARR_API_KEY
```

The dispatcher will connect to each qBittorrent `url` using the WebUI API and use `min_free_gb`, active downloads, bandwidth, and optional node `weight` to score and select nodes.

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Docker

```bash
docker build -t qb-dispatcher .
docker run --rm -p 8000:8000 -v ${PWD}/config.yaml:/app/config.yaml qb-dispatcher
```

## Docker Compose

For easier local development, you can use the provided [docker-compose.yml](docker-compose.yml):

```bash
docker compose up -d
```

This will:

- Run the dispatcher as `qb-dispatcher` (by default exposed on host port 8001 → container 8000)
- Mount `config.yaml` into the container at `/app/config.yaml` (read/write, for the web configurator)
- Attach it to a `qbnet` bridge network (for co-locating qBittorrent/Sonarr/Radarr containers)

POST `/submit` accepts a JSON body like:

```json
{
	"name": "Movie.Title.2024.2160p",
	"category": "movies-uhd",
	"size_estimate_gb": 68,
	"magnet": "magnet:?xt=urn:btih:..."
}
```

## Sonarr/Radarr integration

Configure Sonarr/Radarr download client as if it were talking to qBittorrent:

- Host: dispatcher hostname or IP
- Port: `8000` (or whatever you expose)
- Use SSL: false (unless you terminate TLS in front)
- Username/Password: any values (dispatcher accepts all on `/api/v2/auth/login`)

The dispatcher implements the qBittorrent endpoints Sonarr/Radarr use for adding torrents:

- `POST /api/v2/auth/login` – always returns `Ok.` and a dummy cookie
- `POST /api/v2/torrents/add` – reads the `urls` and `category` form fields and routes the magnet through the space-aware dispatcher
- `GET /api/v2/app/version` and `/api/v2/app/webapiVersion` – minimal responses so *arr clients recognize the service

Ensure your indexers are configured to use magnet links (the dispatcher currently only supports magnets, not uploaded `.torrent` files).

## Connection checks & test buttons

- qBittorrent nodes
	- `GET /nodes` – performs live connection checks to all configured qBittorrent nodes and reports `reachable` plus scores and exclusion reasons.
	- `/config` UI – each node row has a **Test** button that calls `POST /config/test/node` with the row’s values and shows free space / active downloads or an error.

- Sonarr/Radarr instances
	- Configure `arr_instances` as shown in the configuration section above.
	- `GET /arr` – checks each instance by calling `<url>/system/status` with `X-Api-Key` and returns:
		- `reachable: true/false`
		- `version` (if available)
		- `error` (HTTP status or exception text when unreachable)
	- `/config` UI – each *arr row has a **Test** button that calls `POST /config/test/arr` and shows reachability and version.

## Web UI, decision history, and admin API key

- Dashboard
	- `GET /` – shows:
		- Live node metrics and eligibility
		- A “Dry-run decision” form that hits `POST /debug/decision` to preview routing
		- *arr connectivity summary from `GET /arr`
		- A **Recent decisions** table backed by `GET /decisions` showing the last ~50 submissions.

- Configurator
	- `GET /config` – structured form for dispatcher weights, nodes, and `arr_instances`.
	- `GET /config/json` and `POST /config/json` – JSON config API used by the UI.
	- `GET /config/raw` and `POST /config/raw` – raw YAML view / editor.

- Admin API key
	- If `dispatcher.admin_api_key` is set in [config.yaml](config.yaml), the following endpoints require header `X-API-Key: <value>`:
		- `/submit`, `/nodes`, `/arr`, `/decisions`
		- `/config`, `/config/json`, `/config/raw`, `/config/test/node`, `/config/test/arr`
		- `/debug/decision`
	- qBittorrent-compatible endpoints used by Sonarr/Radarr (`/api/v2/*`) remain open so *arr can connect without the admin key.

## Prometheus metrics

The dispatcher exposes basic Prometheus metrics to help you monitor health and routing behaviour.

- Endpoint
	- `GET /metrics` – Prometheus text format, suitable for scraping.

- Metrics
	- `dispatcher_node_reachable{node="name"}` – 1 if a node is reachable in the last evaluation, 0 otherwise.
	- `dispatcher_node_score{node="name"}` – last computed score for each node.
	- `dispatcher_arr_reachable{name="sonarr-main",type="sonarr"}` – 1 if the Sonarr/Radarr instance is reachable, 0 otherwise.
	- `dispatcher_submission_total{status="accepted|rejected|failed"}` – counter of submissions by outcome.

Point Prometheus at the dispatcher service and scrape `/metrics` on port 8000 inside Docker (or your mapped host port, e.g. 8001).

