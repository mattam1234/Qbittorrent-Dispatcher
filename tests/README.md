# Integration Tests

This directory contains integration tests for the qBittorrent Dispatcher application.

## Running Tests

### Quick Start

To run all integration tests:

```bash
./tests/run_tests.sh
```

This script will:
1. Start the uvicorn server on port 8000
2. Wait for the server to be ready
3. Run all integration tests
4. Clean up by stopping the server
5. Return the test exit code

### Manual Testing

You can also run tests manually:

```bash
# Start the server in one terminal
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Run tests in another terminal
python3 tests/test_integrations.py
```

## Test Coverage

The integration tests cover the following new features added in PR #1:

### Integration Endpoints
- **`GET /integrations/status`** - Verify all integration statuses (n8n, Overseerr, Jellyseerr, Prowlarr, messaging services)

### Request Tracking
- **`GET /request-tracking/all`** - Test the centralized request tracking system with duplicate prevention

### Quality Profiles
- **`GET /quality-profiles`** - Test quality profile retrieval from ARR instances

### Metrics
- **`GET /metrics`** - Verify Prometheus metrics endpoint works correctly

### UI Pages
- **`GET /`** - Test that the dashboard loads
- **`GET /config`** - Test that the configurator loads

## Test Requirements

- Python 3.8+
- httpx (for async HTTP requests)
- The application must be able to start on port 8000

## Notes

- Tests that require external services (qBittorrent nodes, ARR instances) are skipped as they would fail without actual services running
- The tests focus on validating that the new integration features are properly wired up and respond correctly
- Tests use a 30-second timeout to handle endpoints that check external connectivity

## Future Improvements

- Add mock servers for qBittorrent and ARR instances to test those endpoints
- Add unit tests for individual components (messaging, n8n client, request tracker, etc.)
- Add tests for the actual functionality (submitting downloads, duplicate detection, etc.)
- Add performance/load testing
