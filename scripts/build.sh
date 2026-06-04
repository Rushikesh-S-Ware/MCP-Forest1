#!/bin/bash
# Build script for client delivery

echo "Building Nexus MCP Server for production..."

# Build Docker image
docker build -t nexus-mcp-server:latest -f docker/Dockerfile .

# Create distribution package
mkdir -p dist
docker save nexus-mcp-server:latest | gzip > dist/nexus-mcp-server.tar.gz

# Copy configuration
cp mcp.json dist/

echo "Build complete! Files in dist/ folder"