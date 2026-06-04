#!/bin/bash
# Stop all Nexus services

echo "🛑 Stopping Nexus services..."

# Stop MCP server (most common)
docker compose --profile production down

# Or stop everything if any profile is running
docker compose --profile pipeline --profile test --profile production down

echo "✅ All services stopped"
