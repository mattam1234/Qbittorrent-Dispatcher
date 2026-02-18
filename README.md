# Space-Aware qBittorrent Download Dispatcher

FastAPI-based dispatcher that selects the best qBittorrent node based on free disk space, active downloads, and bandwidth, then forwards torrent submissions from *arr apps to the best qBittorrent node.

## ✨ New Features

### 🤖 Enhanced Automation & Integration
- **n8n Integration**: Trigger automated workflows on download events (started, completed, duplicates detected, quality suggestions)
- **Messaging Services**: Send notifications via Discord, Slack, or Telegram for download events
- **Request Tracking**: Centralized tracking of all download requests to prevent duplicates
- **Quality Profile Checking**: Automatically check for better quality matches based on ARR stack profiles
- **Smart Suggestions**: Get suggestions for quality upgrades when better options are available

### 📦 Extended Media Management
- **Overseerr Integration**: Fetch and manage media requests from Overseerr
- **Jellyseerr Integration**: Alternative to Overseerr for media request management
- **Prowlarr Integration**: Enhanced indexer management and search capabilities
- **Duplicate Prevention**: Automatic detection and rejection of duplicate downloads
- **Profile-Based Quality Matching**: Ensure downloads match your configured quality profiles

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

# New: Integrations for enhanced automation
integrations:
	# n8n automation platform
	n8n:
		enabled: false
		webhook_url: http://n8n:5678/webhook/qbittorrent-dispatcher
		api_key: null  # Optional for webhook authentication
	
	# Messaging services for notifications
	messaging_services:
		- name: discord-notifications
			type: discord
			enabled: false
			webhook_url: https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
		
		- name: slack-notifications
			type: slack
			enabled: false
			webhook_url: https://hooks.slack.com/services/YOUR_WEBHOOK_URL
		
		- name: telegram-notifications
			type: telegram
			enabled: false
			bot_token: YOUR_BOT_TOKEN
			chat_id: YOUR_CHAT_ID
	
	# Media request management
	overseerr:
		enabled: false
		url: http://overseerr:5055
		api_key: YOUR_OVERSEERR_API_KEY
	
	jellyseerr:
		enabled: false
		url: http://jellyseerr:5055
		api_key: YOUR_JELLYSEERR_API_KEY
	
	# Indexer management
	prowlarr:
		enabled: false
		url: http://prowlarr:9696
		api_key: YOUR_PROWLARR_API_KEY

# Request tracking and quality management
request_tracking:
	enabled: true
	check_duplicates: true  # Prevent duplicate downloads
	check_quality_profiles: true  # Check for better quality matches
	send_suggestions: true  # Send suggestions for quality upgrades
```

The dispatcher will connect to each qBittorrent `url` using the WebUI API and use `min_free_gb`, active downloads, bandwidth, and optional node `weight` to score and select nodes.

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Testing

Run the integration test suite to verify all features are working:

```bash
./tests/run_tests.sh
```

See [tests/README.md](tests/README.md) for more details on the test suite.

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

## New API Endpoints

### Integration Management

#### Get Integration Status
```http
GET /integrations/status
```
Returns the status of all configured integrations (n8n, Overseerr, Jellyseerr, Prowlarr, messaging services).

**Response:**
```json
{
  "n8n": {
    "enabled": true,
    "connected": true,
    "error": null
  },
  "overseerr": {
    "enabled": true,
    "connected": true,
    "version": "1.33.2",
    "error": null
  },
  "messaging_services": [
    {
      "name": "discord-notifications",
      "type": "discord",
      "enabled": true
    }
  ]
}
```

#### Get Overseerr Requests
```http
GET /integrations/overseerr/requests
```
Fetches pending media requests from Overseerr.

#### Get Jellyseerr Requests
```http
GET /integrations/jellyseerr/requests
```
Fetches pending media requests from Jellyseerr.

#### Get Prowlarr Indexers
```http
GET /integrations/prowlarr/indexers
```
Lists all configured indexers from Prowlarr.

### Request Tracking

#### Get All Tracked Requests
```http
GET /request-tracking/all
```
Returns all tracked download requests with duplicate prevention status.

**Response:**
```json
{
  "count": 10,
  "requests": [
    {
      "name": "Movie.Title.2024.2160p",
      "category": "movies-uhd",
      "size_gb": 68.5,
      "timestamp": "2024-01-30T10:00:00",
      "source": "radarr-main",
      "quality_profile": "Ultra HD",
      "selected_node": "qbittorrent-1",
      "status": "downloading"
    }
  ]
}
```

#### Get Requests by Category
```http
GET /request-tracking/category/{category}
```
Returns tracked requests filtered by category.

### Quality Profiles

#### Get Quality Profiles
```http
GET /quality-profiles
```
Returns quality profiles from all configured ARR instances.

**Response:**
```json
{
  "sonarr-main": [
    {
      "id": 1,
      "name": "Ultra HD",
      "cutoff": 20,
      "upgrade_allowed": true
    }
  ],
  "radarr-main": [
    {
      "id": 1,
      "name": "Ultra HD",
      "cutoff": 20,
      "upgrade_allowed": true
    }
  ]
}
```

## Features in Action

### Duplicate Prevention
When a duplicate download is detected:
1. The request is automatically rejected
2. A notification is sent via configured messaging services
3. An n8n webhook is triggered with duplicate details
4. The existing download information is returned

### Quality Suggestions
When a better quality option is available:
1. The current download proceeds as normal
2. A suggestion notification is sent
3. An n8n webhook is triggered with suggestion details
4. The suggestion includes the reason based on ARR profiles

### Centralized Request Tracking
All downloads are tracked centrally:
- Prevents duplicate submissions across different ARR instances
- Tracks download status (pending, downloading, completed, failed)
- Provides a complete audit trail of all requests
- Can be queried by category or status

### Automated Workflows with n8n
Configure n8n to receive webhooks for:
- `download_started`: When a download begins on a node
- `download_completed`: When a download finishes (requires external monitoring)
- `duplicate_detected`: When a duplicate is rejected
- `quality_suggestion`: When a better quality is suggested

Example n8n workflow triggers:
- Send additional notifications to other platforms
- Update a database of downloads
- Trigger post-processing scripts
- Update status dashboards

### Messaging Notifications
Configure Discord, Slack, or Telegram to receive:
- Download started notifications with node and size information
- Duplicate detection warnings
- Quality upgrade suggestions
- Download rejection alerts

## Example Use Cases

### Centralized Media Server Management
1. Configure Sonarr, Radarr, and multiple qBittorrent nodes
2. Enable Overseerr or Jellyseerr for user requests
3. Set up request tracking to prevent duplicate downloads
4. Configure quality profile checking for optimal downloads
5. Use Prowlarr for unified indexer management

### Automated Notification System
1. Configure Discord webhook for team notifications
2. Enable n8n integration for complex workflows
3. Set up Telegram notifications for mobile alerts
4. Receive notifications for all download events

### Quality-Focused Setup
1. Configure quality profiles in Sonarr/Radarr
2. Enable quality profile checking
3. Enable suggestions to get notified of better options
4. Review suggestions and manually upgrade when desired


