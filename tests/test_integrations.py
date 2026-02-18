"""
Basic integration tests for the qBittorrent Dispatcher.
Tests new features: n8n integration, messaging, request tracking, quality profiles.
"""
import asyncio
import httpx


async def test_health_endpoint():
    """Test that the health endpoint is working."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        print("✓ Health endpoint working")


async def test_integrations_status():
    """Test the integrations status endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/integrations/status")
        assert response.status_code == 200
        data = response.json()
        
        # Check that all expected integrations are present
        assert "n8n" in data
        assert "overseerr" in data
        assert "jellyseerr" in data
        assert "prowlarr" in data
        assert "messaging_services" in data
        
        # Check messaging services structure
        assert isinstance(data["messaging_services"], list)
        assert len(data["messaging_services"]) == 3  # Discord, Slack, Telegram
        
        print("✓ Integrations status endpoint working")


async def test_request_tracking():
    """Test the request tracking endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/request-tracking/all")
        assert response.status_code == 200
        data = response.json()
        
        # Should have count and requests
        assert "count" in data
        assert "requests" in data
        assert isinstance(data["requests"], list)
        assert data["count"] == len(data["requests"])
        
        print("✓ Request tracking endpoint working")


async def test_quality_profiles():
    """Test the quality profiles endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/quality-profiles")
        assert response.status_code == 200
        data = response.json()
        
        # Should have profiles for sonarr and radarr instances
        assert "sonarr-main" in data
        assert "radarr-main" in data
        assert isinstance(data["sonarr-main"], list)
        assert isinstance(data["radarr-main"], list)
        
        print("✓ Quality profiles endpoint working")


async def test_nodes_endpoint():
    """Test the nodes endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get("http://127.0.0.1:8000/nodes")
        assert response.status_code == 200
        data = response.json()
        
        # Should return a list of nodes
        assert isinstance(data, list)
        # Config has 2 nodes defined
        assert len(data) == 2
        
        print("✓ Nodes endpoint working")


async def test_arr_instances():
    """Test the ARR instances endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get("http://127.0.0.1:8000/arr")
        assert response.status_code == 200
        data = response.json()
        
        # Should return a list of ARR instances
        assert isinstance(data, list)
        # Config has 2 ARR instances
        assert len(data) == 2
        
        print("✓ ARR instances endpoint working")


async def test_dashboard_ui():
    """Test that the dashboard UI loads."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/")
        assert response.status_code == 200
        html = response.text
        
        # Check for key elements in the dashboard
        assert "<title>qBittorrent Dispatcher Dashboard</title>" in html
        assert "Decisions" in html or "Decision" in html
        
        print("✓ Dashboard UI loads correctly")


async def test_config_ui():
    """Test that the configurator UI loads."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/config")
        assert response.status_code == 200
        html = response.text
        
        # Check for key elements in the configurator
        assert "<title>Dispatcher Configurator</title>" in html
        # Just check that we have some config-related content
        assert "node" in html.lower() or "config" in html.lower()
        
        print("✓ Config UI loads correctly")


async def test_metrics_endpoint():
    """Test the Prometheus metrics endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:8000/metrics")
        assert response.status_code == 200
        text = response.text
        
        # Check for expected metrics
        assert "dispatcher_node_reachable" in text
        assert "dispatcher_arr_reachable" in text
        assert "dispatcher_submission_total" in text
        
        print("✓ Metrics endpoint working")


async def run_all_tests():
    """Run all integration tests."""
    print("\nRunning integration tests...")
    print("=" * 50)
    
    # Only test endpoints that don't require external services
    tests = [
        test_health_endpoint,
        test_integrations_status,
        test_request_tracking,
        test_quality_profiles,
        # Skip nodes and arr tests as they require actual qBittorrent/ARR instances
        # test_nodes_endpoint,
        # test_arr_instances,
        test_dashboard_ui,
        test_config_ui,
        test_metrics_endpoint,
    ]
    
    for test in tests:
        try:
            await test()
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            raise
    
    print("=" * 50)
    print("All tests passed! ✓")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
