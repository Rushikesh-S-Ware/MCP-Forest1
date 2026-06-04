#!/bin/bash
# Full Data Pipeline - Run When Excel File Changes

set -e

echo "🔄 Rebuilding Data Pipeline..."
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Warn user
echo -e "${YELLOW}⚠️  This will:"
echo "   1. Stop MCP server"
echo "   2. Delete existing database"
echo "   3. Reprocess Excel file"
echo ""
read -p "Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled"
    exit 0
fi

# Step 1: Clean
echo -e "${BLUE}Step 1: Stopping services and cleaning database...${NC}"
docker compose down -v
echo "✅ Clean"
echo ""

# Step 2: Pipeline
echo -e "${BLUE}Step 2: Running data pipeline...${NC}"
docker compose --profile pipeline up
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Pipeline completed successfully${NC}"
else
    echo "❌ Pipeline failed"
    exit 1
fi
echo ""

echo "=================================================="
echo "✅ Data rebuild complete!"
echo "=================================================="
echo ""
echo "Next steps:"
echo "   ./start-system.sh    # Start MCP server"
echo ""
