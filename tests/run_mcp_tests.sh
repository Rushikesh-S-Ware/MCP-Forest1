#!/bin/bash
# Comprehensive MCP test runner

set -e

echo "=================================================="
echo "  MCP Comprehensive Test Suite"
echo "=================================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Test database must exist
if [ ! -f "data/processed/forest.db" ]; then
    echo -e "${RED}❌ Database not found${NC}"
    echo "Run pipeline first: docker-compose --profile pipeline up"
    exit 1
fi

echo -e "${GREEN}✅ Database found${NC}"
echo ""

# Run test suites in order
echo "=== 1. Router Tests ==="
pytest tests/mcp/test_router.py -v --tb=short
echo ""

echo "=== 2. Guard Rails (Security) Tests ==="
pytest tests/mcp/test_guard_rails.py -v --tb=short
echo ""

echo "=== 3. Query Quality Tests ==="
pytest tests/mcp/test_query_quality.py -v --tb=short
echo ""

echo "=== 4. Integration Tests ==="
pytest tests/mcp/test_mcp_integration.py -v --tb=short
echo ""

echo "=================================================="
echo -e "${GREEN}✅ All MCP tests completed${NC}"
echo "=================================================="