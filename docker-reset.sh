#!/bin/bash
# Save as docker-reset.sh

# Just remove containers and volumes, keep images
docker-compose down -v
docker rm -f $(docker ps -a | grep nexus | awk '{print $1}') 2>/dev/null || true
docker volume rm nexus-mcp_processed-data 2>/dev/null || true
echo "✅ Reset complete - images preserved for faster rebuild"