#!/bin/bash

# LLM Council - Start script

echo "Starting LLM Council..."
echo ""

# Start backend (bind to all interfaces)
echo "Starting backend on http://0.0.0.0:8001..."
cd /opt/llm-council-test
uv run python -m backend.main --host 0.0.0.0 --port 8001 &
BACKEND_PID=$!

# Wait a bit for backend to start
sleep 2

# Start frontend (bind to all interfaces)
echo "Starting frontend on http://0.0.0.0:5173..."
cd /opt/llm-council-test/frontend
npm run dev -- --host 0.0.0.0 --port 5173 &
FRONTEND_PID=$!

echo ""
echo "✓ LLM Council is running!"
echo "  Backend:  http://0.0.0.0:8001"
echo "  Frontend: http://0.0.0.0:5173"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for Ctrl+C
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
