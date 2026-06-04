#!/bin/bash
# Clean Docker resources for Nexus project

set -e

echo "🧹 Cleaning Nexus Docker resources..."
echo ""

# Stop all Nexus containers
echo "Stopping containers..."
docker compose --profile pipeline --profile test --profile production down 2>/dev/null || true

# Remove containers
echo "Removing containers..."
docker rm -f nexus-pipeline nexus-test nexus-mcp-server 2>/dev/null || true

# Remove images
echo "Removing images..."
docker rmi -f nexus-pipeline:latest nexus-test:latest nexus-mcp-server:latest 2>/dev/null || true

# Remove volumes (optional - asks first)
read -p "Remove volumes (will delete database)? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Removing volumes..."
    docker volume rm gmu_daen_2025_02_a_processed-data 2>/dev/null || true
    docker volume prune -f
fi

# Clean build cache
echo "Cleaning build cache..."
docker builder prune -f

echo ""
echo "✅ Docker cleanup complete!"
