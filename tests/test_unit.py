"""
Unit tests for the qBittorrent Dispatcher application.

Tests core components without requiring external services.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import parse_config, AppConfig, DispatcherSettings, NodeConfig
from app.dispatcher import Dispatcher
from app.models import SubmitRequest, NodeMetrics
from app.request_tracker import RequestTracker
from app.quality_checker import QualityProfileChecker


# ─── Fixtures ────────────────────────────────────────────────────────────────

def make_config(extra: dict | None = None) -> AppConfig:
    """Return a minimal valid AppConfig."""
    raw = {
        "dispatcher": {
            "disk_weight": 1.0,
            "download_weight": 2.0,
            "bandwidth_weight": 0.1,
            "max_downloads": 50,
            "min_score": -1.0,
        },
        "nodes": [
            {
                "name": "node-a",
                "url": "http://localhost:8080",
                "username": "admin",
                "password": "secret",
                "min_free_gb": 0.0,
            },
            {
                "name": "node-b",
                "url": "http://localhost:8081",
                "username": "admin",
                "password": "secret",
                "min_free_gb": 0.0,
            },
        ],
    }
    if extra:
        raw.update(extra)
    return parse_config(raw)


def make_submit_request(**kwargs) -> SubmitRequest:
    defaults = {
        "name": "Test.Movie.2024.1080p",
        "category": "movies",
        "size_estimate_gb": 10.0,
        "magnet": "magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1",
    }
    defaults.update(kwargs)
    return SubmitRequest(**defaults)


# ─── Config parsing tests ─────────────────────────────────────────────────────

class TestConfigParsing:
    def test_minimal_valid_config(self):
        config = make_config()
        assert len(config.nodes) == 2
        assert config.nodes[0].name == "node-a"
        assert config.dispatcher.disk_weight == 1.0

    def test_defaults_applied(self):
        raw = {
            "dispatcher": {},
            "nodes": [
                {"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}
            ],
        }
        config = parse_config(raw)
        assert config.dispatcher.disk_weight == 1.0
        assert config.dispatcher.download_weight == 2.0
        assert config.dispatcher.max_downloads == 50
        assert config.dispatcher.min_score == -1.0
        assert config.nodes[0].min_free_gb == 0.0
        assert config.nodes[0].weight == 1.0

    def test_no_nodes_raises(self):
        with pytest.raises(ValueError, match="No nodes configured"):
            parse_config({"dispatcher": {}, "nodes": []})

    def test_arr_instances_parsed(self):
        raw = {
            "dispatcher": {},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
            "arr_instances": [
                {"name": "sonarr-main", "type": "sonarr", "url": "http://s:8989/api/v3", "api_key": "key123"},
            ],
        }
        config = parse_config(raw)
        assert len(config.arr_instances) == 1
        assert config.arr_instances[0].name == "sonarr-main"
        assert config.arr_instances[0].type == "sonarr"

    def test_integrations_parsed(self):
        raw = {
            "dispatcher": {},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
            "integrations": {
                "n8n": {"enabled": True, "webhook_url": "http://n8n:5678/webhook"},
                "messaging_services": [
                    {"name": "discord", "type": "discord", "enabled": True, "webhook_url": "https://discord.com/hook"},
                ],
            },
        }
        config = parse_config(raw)
        assert config.integrations.n8n.enabled is True
        assert config.integrations.n8n.webhook_url == "http://n8n:5678/webhook"
        assert len(config.integrations.messaging_services) == 1
        assert config.integrations.messaging_services[0].name == "discord"

    def test_request_tracking_parsed(self):
        raw = {
            "dispatcher": {},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
            "request_tracking": {
                "enabled": False,
                "check_duplicates": False,
            },
        }
        config = parse_config(raw)
        assert config.request_tracking.enabled is False
        assert config.request_tracking.check_duplicates is False
        # Defaults for unspecified fields
        assert config.request_tracking.check_quality_profiles is True

    def test_node_weight_default(self):
        raw = {
            "dispatcher": {},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
        }
        config = parse_config(raw)
        assert config.nodes[0].weight == 1.0

    def test_admin_api_key_parsed(self):
        raw = {
            "dispatcher": {"admin_api_key": "supersecret"},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
        }
        config = parse_config(raw)
        assert config.dispatcher.admin_api_key == "supersecret"


# ─── Dispatcher scoring tests ─────────────────────────────────────────────────

class TestDispatcherScoring:
    def _make_dispatcher(self, **config_kwargs) -> Dispatcher:
        config = make_config()
        return Dispatcher(config)

    def test_score_node_basic(self):
        config = make_config()
        dispatcher = Dispatcher(config)
        node = config.nodes[0]

        from app.qb_client import NodeState
        state = NodeState(
            free_disk_gb=1000.0,
            active_downloads=5,
            paused_downloads=0,
            global_download_rate_mbps=10.0,
        )
        metrics = NodeMetrics(
            name=node.name,
            free_disk_gb=state.free_disk_gb,
            active_downloads=state.active_downloads,
            paused_downloads=state.paused_downloads,
            global_download_rate_mbps=state.global_download_rate_mbps,
            reachable=True,
        )

        scored = dispatcher._score_node(node, state, metrics, size_estimate_gb=0.0)
        assert not scored.excluded
        # score = 1000*1.0 - 5*2.0 - 10*0.1 = 1000 - 10 - 1 = 989
        assert scored.score == pytest.approx(989.0)

    def test_score_node_below_min_free(self):
        raw = {
            "dispatcher": {},
            "nodes": [
                {"name": "n1", "url": "http://x:8080", "username": "u", "password": "p", "min_free_gb": 500.0}
            ],
        }
        config = parse_config(raw)
        dispatcher = Dispatcher(config)
        node = config.nodes[0]

        from app.qb_client import NodeState
        state = NodeState(
            free_disk_gb=100.0,  # below min_free_gb=500
            active_downloads=0,
            paused_downloads=0,
            global_download_rate_mbps=0.0,
        )
        metrics = NodeMetrics(
            name=node.name,
            free_disk_gb=state.free_disk_gb,
            active_downloads=0,
            paused_downloads=0,
            global_download_rate_mbps=0.0,
            reachable=True,
        )

        scored = dispatcher._score_node(node, state, metrics)
        assert scored.excluded
        assert scored.metrics.excluded_reason == "below_min_free_space"

    def test_score_node_too_many_downloads(self):
        raw = {
            "dispatcher": {"max_downloads": 5},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
        }
        config = parse_config(raw)
        dispatcher = Dispatcher(config)
        node = config.nodes[0]

        from app.qb_client import NodeState
        state = NodeState(
            free_disk_gb=1000.0,
            active_downloads=10,  # exceeds max_downloads=5
            paused_downloads=0,
            global_download_rate_mbps=0.0,
        )
        metrics = NodeMetrics(
            name=node.name,
            free_disk_gb=1000.0,
            active_downloads=10,
            paused_downloads=0,
            global_download_rate_mbps=0.0,
            reachable=True,
        )

        scored = dispatcher._score_node(node, state, metrics)
        assert scored.excluded
        assert scored.metrics.excluded_reason == "too_many_downloads"

    def test_score_node_size_estimate_reduces_free_space(self):
        raw = {
            "dispatcher": {},
            "nodes": [
                {"name": "n1", "url": "http://x:8080", "username": "u", "password": "p", "min_free_gb": 100.0}
            ],
        }
        config = parse_config(raw)
        dispatcher = Dispatcher(config)
        node = config.nodes[0]

        from app.qb_client import NodeState
        state = NodeState(
            free_disk_gb=150.0,
            active_downloads=0,
            paused_downloads=0,
            global_download_rate_mbps=0.0,
        )
        metrics = NodeMetrics(
            name=node.name,
            free_disk_gb=150.0,
            active_downloads=0,
            paused_downloads=0,
            global_download_rate_mbps=0.0,
            reachable=True,
        )

        # With 60 GB estimate, effective free = 150 - 60 = 90 < min_free_gb=100
        scored = dispatcher._score_node(node, state, metrics, size_estimate_gb=60.0)
        assert scored.excluded
        assert scored.metrics.excluded_reason == "below_min_free_space"

    def test_score_node_weight_multiplier(self):
        raw = {
            "dispatcher": {"disk_weight": 1.0, "download_weight": 0.0, "bandwidth_weight": 0.0},
            "nodes": [
                {"name": "n1", "url": "http://x:8080", "username": "u", "password": "p", "weight": 2.0}
            ],
        }
        config = parse_config(raw)
        dispatcher = Dispatcher(config)
        node = config.nodes[0]

        from app.qb_client import NodeState
        state = NodeState(free_disk_gb=500.0, active_downloads=0, paused_downloads=0, global_download_rate_mbps=0.0)
        metrics = NodeMetrics(name=node.name, free_disk_gb=500.0, active_downloads=0, paused_downloads=0, global_download_rate_mbps=0.0, reachable=True)

        scored = dispatcher._score_node(node, state, metrics)
        # base_score = 500 * 1.0 = 500, then * weight 2.0 = 1000
        assert scored.score == pytest.approx(1000.0)

    def test_score_node_score_below_minimum(self):
        raw = {
            "dispatcher": {"min_score": 1000.0, "disk_weight": 1.0, "download_weight": 0.0, "bandwidth_weight": 0.0},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
        }
        config = parse_config(raw)
        dispatcher = Dispatcher(config)
        node = config.nodes[0]

        from app.qb_client import NodeState
        state = NodeState(free_disk_gb=100.0, active_downloads=0, paused_downloads=0, global_download_rate_mbps=0.0)
        metrics = NodeMetrics(name=node.name, free_disk_gb=100.0, active_downloads=0, paused_downloads=0, global_download_rate_mbps=0.0, reachable=True)

        scored = dispatcher._score_node(node, state, metrics)
        # score = 100 < min_score 1000
        assert scored.excluded
        assert scored.metrics.excluded_reason == "score_below_minimum"

    def test_get_decisions_empty(self):
        config = make_config()
        dispatcher = Dispatcher(config)
        assert dispatcher.get_decisions() == []

    def test_get_decisions_with_limit(self):
        config = make_config()
        dispatcher = Dispatcher(config)
        req = make_submit_request()

        from app.models import SubmitDecision
        decision = SubmitDecision(selected_node="node-a", reason="highest_score", status="accepted", attempted_nodes=[])
        for _ in range(10):
            dispatcher._record_decision(req, decision)

        assert len(dispatcher.get_decisions(limit=5)) == 5
        assert len(dispatcher.get_decisions(limit=10)) == 10
        assert len(dispatcher.get_decisions(limit=0)) == 0

    @pytest.mark.asyncio
    async def test_submit_no_eligible_nodes(self):
        config = make_config()
        dispatcher = Dispatcher(config)
        req = make_submit_request()

        # Mock evaluate_nodes to return only excluded nodes
        from app.dispatcher import ScoredNode
        from app.qb_client import QbittorrentNodeClient

        mock_metrics = NodeMetrics(
            name="node-a", free_disk_gb=None, active_downloads=0,
            paused_downloads=0, global_download_rate_mbps=0.0,
            reachable=False, excluded_reason="api_unreachable",
        )
        excluded_node = ScoredNode(
            config=config.nodes[0],
            client=dispatcher._clients["node-a"],
            state=None,
            metrics=mock_metrics,
            score=None,
            excluded=True,
        )

        with patch.object(dispatcher, "evaluate_nodes", AsyncMock(return_value=[excluded_node])):
            decision = await dispatcher.submit(req)

        assert decision.status == "rejected"
        assert decision.reason == "no_eligible_nodes"


# ─── Request tracker tests ────────────────────────────────────────────────────

class TestRequestTracker:
    def test_add_and_get_request(self):
        tracker = RequestTracker()
        req = make_submit_request()
        request_id = tracker.add_request(req, source="sonarr")
        assert request_id is not None
        tracked = tracker.get_request(request_id)
        assert tracked is not None
        assert tracked.name == req.name
        assert tracked.category == req.category
        assert tracked.source == "sonarr"
        assert tracked.status == "pending"

    def test_duplicate_detection(self):
        tracker = RequestTracker()
        req = make_submit_request()
        tracker.add_request(req)

        is_dup, existing = tracker.is_duplicate(req)
        assert is_dup is True
        assert existing is not None
        assert existing.name == req.name

    def test_no_duplicate_for_different_magnet(self):
        tracker = RequestTracker()
        req1 = make_submit_request(magnet="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1")
        req2 = make_submit_request(magnet="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2")
        tracker.add_request(req1)

        is_dup, existing = tracker.is_duplicate(req2)
        assert is_dup is False
        assert existing is None

    def test_update_status(self):
        tracker = RequestTracker()
        req = make_submit_request()
        request_id = tracker.add_request(req)

        tracker.update_status(request_id, "downloading", "node-a")
        tracked = tracker.get_request(request_id)
        assert tracked.status == "downloading"
        assert tracked.selected_node == "node-a"

    def test_get_all_requests(self):
        tracker = RequestTracker()
        req1 = make_submit_request(magnet="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        req2 = make_submit_request(magnet="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        tracker.add_request(req1)
        tracker.add_request(req2)

        all_reqs = tracker.get_all_requests()
        assert len(all_reqs) == 2

    def test_get_requests_by_category(self):
        tracker = RequestTracker()
        req_movies = make_submit_request(
            category="movies",
            magnet="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        req_tv = make_submit_request(
            category="tv",
            magnet="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        tracker.add_request(req_movies)
        tracker.add_request(req_tv)

        movies = tracker.get_requests_by_category("movies")
        assert len(movies) == 1
        assert movies[0].category == "movies"

        tv = tracker.get_requests_by_category("tv")
        assert len(tv) == 1
        assert tv[0].category == "tv"

    def test_cleanup_old_requests(self):
        tracker = RequestTracker()
        req = make_submit_request()
        request_id = tracker.add_request(req)

        # Manually age the request
        tracker._requests[request_id].timestamp = datetime.now() - timedelta(days=10)

        removed = tracker.cleanup_old_requests(days=7)
        assert removed == 1
        assert tracker.get_request(request_id) is None

    def test_cleanup_keeps_recent_requests(self):
        tracker = RequestTracker()
        req = make_submit_request()
        tracker.add_request(req)

        removed = tracker.cleanup_old_requests(days=7)
        assert removed == 0
        assert len(tracker.get_all_requests()) == 1

    def test_generate_request_id_from_btih(self):
        tracker = RequestTracker()
        magnet = "magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1&dn=test"
        req_id = tracker._generate_request_id(magnet)
        assert req_id == "abc123def456abc123def456abc123def456abc1"

    def test_generate_request_id_fallback(self):
        tracker = RequestTracker()
        magnet = "magnet:?xt=urn:other:somethingelse"
        req_id = tracker._generate_request_id(magnet)
        # Should return a sha1 hex of the magnet
        import hashlib
        expected = hashlib.sha1(magnet.encode()).hexdigest()
        assert req_id == expected

    def test_duplicate_not_detected_after_24h(self):
        tracker = RequestTracker()
        req = make_submit_request()
        request_id = tracker.add_request(req)

        # Age the request to more than 24 hours ago
        tracker._requests[request_id].timestamp = datetime.now() - timedelta(hours=25)

        is_dup, existing = tracker.is_duplicate(req)
        assert is_dup is False


# ─── Quality checker tests ────────────────────────────────────────────────────

class TestQualityChecker:
    def test_parse_quality_2160p(self):
        checker = QualityProfileChecker([])
        assert checker._parse_quality_from_name("Movie.2024.2160p.BluRay") == "2160p"

    def test_parse_quality_4k(self):
        checker = QualityProfileChecker([])
        assert checker._parse_quality_from_name("Movie.2024.4K.HDR") == "2160p"

    def test_parse_quality_uhd(self):
        checker = QualityProfileChecker([])
        assert checker._parse_quality_from_name("Movie.2024.UHD.BluRay") == "2160p"

    def test_parse_quality_1080p(self):
        checker = QualityProfileChecker([])
        assert checker._parse_quality_from_name("Movie.2024.1080p.WEB-DL") == "1080p"

    def test_parse_quality_720p(self):
        checker = QualityProfileChecker([])
        assert checker._parse_quality_from_name("Movie.2024.720p.BluRay") == "720p"

    def test_parse_quality_unknown(self):
        checker = QualityProfileChecker([])
        assert checker._parse_quality_from_name("Movie.2024.DVDRip") is None

    def test_get_arr_for_movie_category(self):
        from app.config import ArrInstanceConfig
        sonarr = ArrInstanceConfig(name="sonarr", type="sonarr", url="http://s:8989/api/v3", api_key="k")
        radarr = ArrInstanceConfig(name="radarr", type="radarr", url="http://r:7878/api/v3", api_key="k")
        checker = QualityProfileChecker([sonarr, radarr])
        result = checker._get_arr_for_category("movies-uhd")
        assert result is not None
        assert result.type == "radarr"

    def test_get_arr_for_tv_category(self):
        from app.config import ArrInstanceConfig
        sonarr = ArrInstanceConfig(name="sonarr", type="sonarr", url="http://s:8989/api/v3", api_key="k")
        radarr = ArrInstanceConfig(name="radarr", type="radarr", url="http://r:7878/api/v3", api_key="k")
        checker = QualityProfileChecker([sonarr, radarr])
        result = checker._get_arr_for_category("tv-shows")
        assert result is not None
        assert result.type == "sonarr"

    def test_get_arr_for_unknown_category_returns_first(self):
        from app.config import ArrInstanceConfig
        sonarr = ArrInstanceConfig(name="sonarr", type="sonarr", url="http://s:8989/api/v3", api_key="k")
        checker = QualityProfileChecker([sonarr])
        result = checker._get_arr_for_category("default")
        assert result is sonarr

    def test_get_arr_for_empty_instances(self):
        checker = QualityProfileChecker([])
        result = checker._get_arr_for_category("movies")
        assert result is None


# ─── FastAPI app integration tests (no external services) ─────────────────────

@pytest.fixture
def app():
    from app.main import create_app
    config = make_config()
    return create_app(config)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestApiEndpoints:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_qb_login(self, client):
        resp = client.post("/api/v2/auth/login", data={"username": "admin", "password": "pass"})
        assert resp.status_code == 200
        assert resp.text == "Ok."

    def test_qb_app_version(self, client):
        resp = client.get("/api/v2/app/version")
        assert resp.status_code == 200
        assert "dispatcher" in resp.text

    def test_qb_webapi_version(self, client):
        resp = client.get("/api/v2/app/webapiVersion")
        assert resp.status_code == 200
        assert resp.text == "2.8.18"

    def test_decisions_empty(self, client):
        resp = client.get("/decisions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "dispatcher_submission_total" in resp.text

    def test_dashboard_ui(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "qBittorrent Dispatcher Dashboard" in resp.text

    def test_config_ui(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200
        assert "Dispatcher Configurator" in resp.text

    def test_integrations_status(self, client):
        resp = client.get("/integrations/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "n8n" in data
        assert "overseerr" in data
        assert "jellyseerr" in data
        assert "prowlarr" in data
        assert "messaging_services" in data

    def test_request_tracking_all(self, client):
        resp = client.get("/request-tracking/all")
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "requests" in data
        assert data["count"] == 0

    def test_request_tracking_by_category(self, client):
        resp = client.get("/request-tracking/category/movies")
        assert resp.status_code == 200
        data = resp.json()
        assert "category" in data
        assert data["category"] == "movies"

    def test_quality_profiles(self, client):
        resp = client.get("/quality-profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_config_test_node_registered(self, app):
        """Verify /config/test/node is properly registered (not dead code)."""
        routes = {r.path for r in app.routes}
        assert "/config/test/node" in routes

    def test_config_test_arr_registered(self, app):
        """Verify /config/test/arr is properly registered (not dead code)."""
        routes = {r.path for r in app.routes}
        assert "/config/test/arr" in routes

    def test_config_test_node_unreachable_node(self, app):
        """Test that /config/test/node returns unreachable when the node raises an exception."""
        from fastapi.testclient import TestClient
        from unittest.mock import patch

        payload = {
            "name": "test-node",
            "url": "http://localhost:19999",
            "username": "admin",
            "password": "secret",
            "min_free_gb": 0.0,
        }

        with patch("app.main.QbittorrentNodeClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.fetch_state.side_effect = ConnectionError("Connection refused")
            mock_cls.return_value = mock_instance

            client = TestClient(app)
            resp = client.post("/config/test/node", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"]["name"] == "test-node"
        assert data["metrics"]["reachable"] is False
        assert data["excluded"] is True

    def test_config_test_arr_unreachable(self, app):
        """Test that /config/test/arr returns unreachable when the arr instance raises an exception."""
        from fastapi.testclient import TestClient
        from unittest.mock import patch, AsyncMock
        from app.arr_client import ArrInstanceState

        payload = {
            "name": "test-sonarr",
            "type": "sonarr",
            "url": "http://localhost:19999/api/v3",
            "api_key": "testkey",
        }

        unreachable_state = ArrInstanceState(reachable=False, version=None, error="Connection refused")

        with patch("app.main.check_arr_instance", AsyncMock(return_value=unreachable_state)):
            client = TestClient(app)
            resp = client.post("/config/test/arr", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-sonarr"
        assert data["reachable"] is False
        assert data["error"] == "Connection refused"

    def test_admin_api_key_required(self):
        """Test that endpoints require X-API-Key when admin_api_key is configured."""
        raw = {
            "dispatcher": {"admin_api_key": "mysecretkey"},
            "nodes": [{"name": "n1", "url": "http://x:8080", "username": "u", "password": "p"}],
        }
        from app.main import create_app
        from app.config import parse_config
        from fastapi.testclient import TestClient
        config = parse_config(raw)
        app = create_app(config)
        client = TestClient(app)

        # Without key should get 401
        resp = client.get("/nodes")
        assert resp.status_code == 401

        # With correct key should be allowed
        resp = client.get("/nodes", headers={"X-API-Key": "mysecretkey"})
        # Will still fail if nodes are unreachable, but we should get through auth (200 or timeout)
        assert resp.status_code != 401

    def test_qb_torrents_add_no_urls(self, client):
        resp = client.post("/api/v2/torrents/add", data={"urls": "", "category": "movies"})
        assert resp.status_code == 400

    def test_qb_torrents_add_non_magnet(self, client):
        resp = client.post("/api/v2/torrents/add", data={"urls": "http://example.com/file.torrent", "category": "movies"})
        assert resp.status_code == 400
