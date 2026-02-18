#!/bin/bash

# Start the server in the background
cd /home/runner/work/Qbittorrent-Dispatcher/Qbittorrent-Dispatcher
uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/test_server.log 2>&1 &
SERVER_PID=$!

# Give the server time to start
sleep 5

# Run the tests
python3 tests/test_integrations.py
TEST_EXIT=$?

# Cleanup: kill the server
kill $SERVER_PID 2>/dev/null || true

# Exit with the test exit code
exit $TEST_EXIT
