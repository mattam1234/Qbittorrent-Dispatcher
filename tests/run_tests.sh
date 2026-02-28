#!/bin/bash

# Get the directory where the script is located and navigate to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Run pytest unit tests first (no server required)
echo "Running unit tests..."
python3 -m pytest tests/test_unit.py -v
UNIT_EXIT=$?

if [ $UNIT_EXIT -ne 0 ]; then
    echo "Unit tests failed!"
    exit $UNIT_EXIT
fi

# Start the server in the background
uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/test_server.log 2>&1 &
SERVER_PID=$!

# Give the server time to start
sleep 5

# Run the integration tests
python3 tests/test_integrations.py
TEST_EXIT=$?

# Cleanup: kill the server
kill $SERVER_PID 2>/dev/null || true

# Exit with the integration test exit code
exit $TEST_EXIT
