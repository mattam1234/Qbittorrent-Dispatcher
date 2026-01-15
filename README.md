# Space-Aware qBittorrent Download Dispatcher

FastAPI-based dispatcher that selects the best qBittorrent node based on free disk space, active downloads, and bandwidth, then forwards torrent submissions from *arr apps to the best qBittorrent node.

## Configuration

Edit [config.yaml](config.yaml) to define dispatcher weights and qBittorrent nodes.

Each qBittorrent node entry looks like:

```yaml
nodes:
	- name: qbittorrent-1        # Friendly name used in logs and UI
		url: http://qbittorrent:8080  # Base URL of the qBittorrent WebUI
		username: admin            # WebUI username
		password: secret           # WebUI password
		min_free_gb: 500           # Node is excluded if free space drops below this
```

The dispatcher will connect to each `url` using the WebUI API and use `min_free_gb`, active downloads, and bandwidth to score and select nodes.

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

- Run the dispatcher as `qb-dispatcher` on port 8000
- Mount `config.yaml` into the container at `/app/config.yaml` (read-only)
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

## Connection checks

- qBittorrent nodes
	- `GET /nodes` – already performs live connection checks to all configured qBittorrent nodes and reports `reachable` plus scores and exclusion reasons.

- Sonarr/Radarr instances
	- Add entries under `arr_instances` in [config.yaml](config.yaml), e.g.:

		```yaml
		arr_instances:
			- name: sonarr-main
				type: sonarr
				url: http://sonarr:8989/api/v3
				api_key: YOUR_SONARR_API_KEY
		```

	- `GET /arr` – checks each instance by calling `<url>/system/status` with `X-Api-Key` and returns:
		- `reachable: true/false`
		- `version` (if available)
		- `error` (HTTP status or exception text when unreachable)

