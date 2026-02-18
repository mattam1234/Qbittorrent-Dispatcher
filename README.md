# Space-Aware qBittorrent Download Dispatcher

FastAPI-based dispatcher that selects the best qBittorrent node based on free disk space, active downloads, and bandwidth, then forwards torrent submissions from *arr apps to the best qBittorrent node.

## Table of Contents

- [Features](#-new-features)
  - [Enhanced Automation & Integration](#-enhanced-automation--integration)
  - [Extended Media Management](#-extended-media-management)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Running Locally](#running-locally)
  - [Docker](#docker)
  - [Docker Compose](#docker-compose)
- [Configuration](#configuration)
- [Sonarr/Radarr Integration](#sonarrradarr-integration)
- [Testing](#testing)
- [API Endpoints](#api-endpoints)
  - [Integration Management](#integration-management)
  - [Request Tracking](#request-tracking)
  - [Quality Profiles](#quality-profiles)
- [Web UI & Admin](#web-ui-decision-history-and-admin-api-key)
- [Monitoring](#prometheus-metrics)
- [Features in Action](#features-in-action)
- [Example Use Cases](#example-use-cases)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

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

## Quick Start

Get up and running in minutes:

### Using Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/mattam1234/Qbittorrent-Dispatcher.git
cd Qbittorrent-Dispatcher

# Edit config.yaml with your qBittorrent nodes and ARR instances
nano config.yaml

# Start the dispatcher
docker compose up -d

# Access the Web UI at http://localhost:8001
```

### Using Docker

```bash
docker build -t qb-dispatcher .
docker run --rm -p 8000:8000 -v ${PWD}/config.yaml:/app/config.yaml qb-dispatcher
```

### Using Python

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## How It Works

The dispatcher acts as a smart proxy between your *arr applications (Sonarr/Radarr) and multiple qBittorrent instances:

1. **Request Reception**: Sonarr/Radarr sends a torrent download request to the dispatcher
2. **Node Evaluation**: The dispatcher evaluates all configured qBittorrent nodes based on:
   - Available disk space (weighted by `disk_weight`)
   - Active downloads (weighted by `download_weight`)
   - Current bandwidth usage (weighted by `bandwidth_weight`)
   - Minimum free space requirements
3. **Smart Selection**: The node with the highest score is selected
4. **Torrent Submission**: The magnet link is forwarded to the selected node
5. **Tracking & Notifications**: The request is tracked and notifications are sent (if configured)

```
┌─────────┐     ┌────────────┐     ┌──────────────┐
│ Sonarr  │────▶│ Dispatcher │────▶│ qBittorrent 1│
└─────────┘     │            │     └──────────────┘
┌─────────┐     │   (Smart   │     ┌──────────────┐
│ Radarr  │────▶│   Routing) │────▶│ qBittorrent 2│
└─────────┘     └────────────┘     └──────────────┘
```

## Prerequisites

- Python 3.12 or higher (for local installation)
- Docker and Docker Compose (for containerized deployment)
- One or more qBittorrent instances with Web UI enabled
- (Optional) Sonarr/Radarr instances
- (Optional) Overseerr/Jellyseerr for media requests
- (Optional) Prowlarr for indexer management
- (Optional) n8n for workflow automation
- (Optional) Discord/Slack/Telegram webhooks for notifications

## Installation

### Running Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t qb-dispatcher .
docker run --rm -p 8000:8000 -v ${PWD}/config.yaml:/app/config.yaml qb-dispatcher
```

### Docker Compose

For easier local development, you can use the provided [docker-compose.yml](docker-compose.yml):

```bash
docker compose up -d
```

This will:

- Run the dispatcher as `qb-dispatcher` (by default exposed on host port 8001 → container 8000)
- Mount `config.yaml` into the container at `/app/config.yaml` (read/write, for the web configurator)
- Attach it to a `qbnet` bridge network (for co-locating qBittorrent/Sonarr/Radarr containers)

## Configuration

Edit [config.yaml](config.yaml) to define dispatcher weights, security, qBittorrent nodes, and optional *arr instances.

### Configuration File Structure

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

### Key Configuration Options

**Dispatcher Weights:**
- `disk_weight`: How much to prioritize nodes with more free disk space (default: 1.0)
- `download_weight`: How much to prioritize nodes with fewer active downloads (default: 2.0)
- `bandwidth_weight`: How much to consider current bandwidth usage (default: 0.1)
- `max_downloads`: Maximum number of active downloads allowed per node
- `min_score`: Minimum acceptable score for a node to be eligible

**Node Settings:**
- `url`: qBittorrent Web UI URL
- `username`/`password`: qBittorrent credentials
- `min_free_gb`: Minimum free disk space required (in GB)
- `weight`: Optional multiplier for this node's score (default: 1.0)

The dispatcher will connect to each qBittorrent `url` using the WebUI API and use `min_free_gb`, active downloads, bandwidth, and optional node `weight` to score and select nodes.

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

**Important**: Ensure your indexers are configured to use magnet links (the dispatcher currently only supports magnets, not uploaded `.torrent` files).

## Testing

Run the integration test suite to verify all features are working:

```bash
./tests/run_tests.sh
```

See [tests/README.md](tests/README.md) for more details on the test suite.

## API Endpoints

### Core Endpoints

#### Submit Download
```http
POST /submit
```
Submit a new download and have the dispatcher pick the best node.

**Request Body:**
```json
{
  "name": "Movie.Title.2024.2160p",
  "category": "movies-uhd",
  "size_estimate_gb": 68,
  "magnet": "magnet:?xt=urn:btih:..."
}
```

**Response:**
```json
{
  "status": "accepted",
  "node": "qbittorrent-1",
  "message": "Torrent submitted successfully"
}
```

#### Get Node Status
```http
GET /nodes
```
Performs live connection checks to all configured qBittorrent nodes and reports reachability, scores, and exclusion reasons.

**Response:**
```json
{
  "nodes": [
    {
      "name": "qbittorrent-1",
      "reachable": true,
      "score": 0.85,
      "free_gb": 1250.5,
      "active_downloads": 3
    }
  ]
}
```

#### Get ARR Status
```http
GET /arr
```
Checks each ARR instance connectivity and returns version information.

#### Get Decision History
```http
GET /decisions
```
Returns the recent download routing decisions (last ~50 submissions).

## Connection Checks & Testing

### qBittorrent Nodes

**API Endpoint:**
- `GET /nodes` – performs live connection checks to all configured qBittorrent nodes and reports `reachable` status, scores, and exclusion reasons.

**Web UI:**
- Navigate to `/config` in your browser
- Each node row has a **Test** button
- Click to test connectivity and view:
  - Free disk space
  - Active downloads
  - Connection status
  - Any errors

### Sonarr/Radarr Instances

Configure `arr_instances` as shown in the configuration section above.

**API Endpoint:**
- `GET /arr` – checks each instance by calling `<url>/system/status` with `X-Api-Key` and returns:
  - `reachable: true/false`
  - `version` (if available)
  - `error` (HTTP status or exception text when unreachable)

**Web UI:**
- Navigate to `/config` in your browser
- Each *arr row has a **Test** button
- Click to verify:
  - Connectivity
  - Version information
  - API key validity

## Web UI, Decision History, and Admin API Key

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

## Troubleshooting

### Common Issues

#### Dispatcher can't connect to qBittorrent nodes

**Symptoms**: Node shows as unreachable in `/nodes` or dashboard

**Solutions**:
- Verify qBittorrent Web UI is enabled (Settings → Web UI)
- Check the URL is accessible from the dispatcher container/host
- Verify username and password are correct
- Check firewall rules allow connections
- Test connectivity: `curl http://qbittorrent:8080/api/v2/app/version`

#### Sonarr/Radarr can't connect to dispatcher

**Symptoms**: Test connection fails in Sonarr/Radarr download client settings

**Solutions**:
- Verify dispatcher is running and accessible
- Check the host and port are correct (e.g., `http://dispatcher:8000`)
- Ensure you're using the qBittorrent client type in Sonarr/Radarr
- Any username/password will work (authentication is always accepted)
- Check logs: `docker logs qb-dispatcher`

#### Downloads always go to the same node

**Symptoms**: Load isn't distributed across nodes

**Solutions**:
- Check if other nodes are being excluded (view `/nodes` endpoint)
- Verify `min_free_gb` requirements are met on all nodes
- Review dispatcher weights in `config.yaml`
- Check if nodes have significantly different disk space (high `disk_weight` favors nodes with more space)
- Adjust `download_weight` to better balance based on active torrents

#### Configuration changes not taking effect

**Symptoms**: Changes to `config.yaml` don't seem to apply

**Solutions**:
- Restart the dispatcher after config changes: `docker restart qb-dispatcher`
- Or use the web configurator at `/config` which applies changes immediately
- Check for YAML syntax errors: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- Verify the config file is mounted correctly in Docker

#### Admin endpoints require authentication but I didn't set it

**Symptoms**: Getting 401 errors on management endpoints

**Solutions**:
- Check if `dispatcher.admin_api_key` is set in `config.yaml`
- Remove or comment out `admin_api_key` to disable authentication
- Or add `X-API-Key` header with the configured value

### Debugging

Enable detailed logging by checking dispatcher logs:

```bash
# Docker
docker logs -f qb-dispatcher

# Local
# Logs will appear in console when running uvicorn
```

Check node connectivity and scores:
```bash
curl http://localhost:8001/nodes
```

View recent routing decisions:
```bash
curl http://localhost:8001/decisions
```

Test the decision logic without actually submitting:
```bash
curl -X POST http://localhost:8001/debug/decision \
  -H "Content-Type: application/json" \
  -d '{"name":"test","category":"movies","size_estimate_gb":50,"magnet":"magnet:?xt=urn:btih:test"}'
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`./tests/run_tests.sh`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development Setup

```bash
# Clone the repo
git clone https://github.com/mattam1234/Qbittorrent-Dispatcher.git
cd Qbittorrent-Dispatcher

# Install dependencies
pip install -r requirements.txt

# Run in development mode
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## License

This project is open source and available under the MIT License.

---

**Need help?** Open an issue on [GitHub](https://github.com/mattam1234/Qbittorrent-Dispatcher/issues)