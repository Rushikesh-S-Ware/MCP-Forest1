#!/bin/bash
# Testing script for development

echo "Starting test environment..."

# Start the test bridge
docker-compose --profile test up -d mcp-test-bridge

echo "Waiting for services to start..."
sleep 5

# Start Streamlit
streamlit run src/nexus/ui/streamlit_app.py