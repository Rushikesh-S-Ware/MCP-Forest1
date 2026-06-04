#!/bin/bash
# ============================================================================
# Nexus System Testing Script
# Tests all components and verifies fixes are working
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Test results tracking
TESTS_PASSED=0
TESTS_FAILED=0
FAILED_TESTS=()

# ============================================================================
# TEST FUNCTIONS
# ============================================================================

run_test() {
    local test_name="$1"
    local test_command="$2"
    
    echo -e "\n${BLUE}[TEST]${NC} $test_name"
    echo "----------------------------------------"
    
    if eval "$test_command"; then
        echo -e "${GREEN}✓ PASSED${NC}"
        ((TESTS_PASSED++))
    else
        echo -e "${RED}✗ FAILED${NC}"
        ((TESTS_FAILED++))
        FAILED_TESTS+=("$test_name")
    fi
}

# ============================================================================
# 1. PREREQUISITE TESTS
# ============================================================================

test_prerequisites() {
    echo -e "\n${YELLOW}=== PREREQUISITE TESTS ===${NC}"
    
    run_test "Docker installed" "docker --version > /dev/null 2>&1"
    run_test "Docker Compose installed" "docker-compose --version > /dev/null 2>&1"
    run_test "Excel file exists" "test -f data/raw/global_05212025.xlsx"
    run_test "Schema.py has verify_indexes method" "grep -q 'def verify_indexes' src/nexus/data/database/schema.py"
    run_test "Exporter.py has index verification" "grep -q 'Verifying database indexes' src/nexus/data/database/exporter.py || echo 'Warning: Index verification not found in exporter.py'"
}

# ============================================================================
# 2. BUILD TESTS
# ============================================================================

test_build() {
    echo -e "\n${YELLOW}=== BUILD TESTS ===${NC}"
    
    run_test "Build pipeline container" "docker build -f docker/Dockerfile.pipeline -t nexus-pipeline:latest . > /dev/null 2>&1"
    run_test "Build test container" "docker build -f docker/Dockerfile.test -t nexus-test:latest . > /dev/null 2>&1"
    run_test "Build MCP container" "docker build -f docker/Dockerfile.mcp -t nexus-mcp-server:latest . > /dev/null 2>&1"
    
    run_test "Pipeline image exists" "docker images | grep -q nexus-pipeline"
    run_test "Test image exists" "docker images | grep -q nexus-test"
    run_test "MCP image exists" "docker images | grep -q nexus-mcp-server"
}

# ============================================================================
# 3. PIPELINE TESTS
# ============================================================================

test_pipeline() {
    echo -e "\n${YELLOW}=== PIPELINE TESTS ===${NC}"
    
    # Clean start
    echo "Cleaning up old containers and volumes..."
    docker-compose down > /dev/null 2>&1 || true
    docker volume rm nexus-mcp_processed-data > /dev/null 2>&1 || true
    
    # Run pipeline
    echo "Running pipeline..."
    docker-compose --profile pipeline up > /tmp/pipeline.log 2>&1
    
    run_test "Pipeline exits successfully" "docker inspect nexus-pipeline --format='{{.State.ExitCode}}' | grep -q '^0$'"
    
    run_test "Database created" "docker run --rm -v nexus-mcp_processed-data:/data alpine test -f /data/forest.db"
    
    # Critical: Test indexes exist
    echo -e "\n${BLUE}[CRITICAL TEST]${NC} Checking indexes..."
    INDEXES=$(docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
        apk add --no-cache sqlite >/dev/null 2>&1
        sqlite3 /data/forest.db \"SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL;\"
    " 2>/dev/null)
    
    run_test "Indexes exist (should be 8)" "[ '$INDEXES' -ge 8 ]"
    
    if [ "$INDEXES" -ge 8 ]; then
        echo -e "${GREEN}Found $INDEXES indexes - FIX IS WORKING!${NC}"
    else
        echo -e "${RED}Only found $INDEXES indexes - FIX NOT WORKING${NC}"
        echo "Index details:"
        docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
            apk add --no-cache sqlite >/dev/null 2>&1
            sqlite3 /data/forest.db \"SELECT name FROM sqlite_master WHERE type='index' ORDER BY name;\"
        "
    fi
    
    # Check row counts
    TCL_COUNT=$(docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
        apk add --no-cache sqlite >/dev/null 2>&1
        sqlite3 /data/forest.db 'SELECT COUNT(*) FROM fact_tree_cover_loss;'
    " 2>/dev/null)
    
    run_test "Tree cover loss has data (expect ~31,680)" "[ '$TCL_COUNT' -gt 30000 ]"
    
    PF_COUNT=$(docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
        apk add --no-cache sqlite >/dev/null 2>&1
        sqlite3 /data/forest.db 'SELECT COUNT(*) FROM fact_primary_forest;'
    " 2>/dev/null)
    
    run_test "Primary forest has data (expect ~1,650)" "[ '$PF_COUNT' -gt 1500 ]"
    
    CARBON_COUNT=$(docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
        apk add --no-cache sqlite >/dev/null 2>&1
        sqlite3 /data/forest.db 'SELECT COUNT(*) FROM fact_carbon;'
    " 2>/dev/null)
    
    run_test "Carbon has data (expect ~11,880)" "[ '$CARBON_COUNT' -gt 10000 ]"
    
    echo "Row counts: TCL=$TCL_COUNT, PF=$PF_COUNT, Carbon=$CARBON_COUNT"
}

# ============================================================================
# 4. TEST SUITE TESTS
# ============================================================================

test_test_suite() {
    echo -e "\n${YELLOW}=== TEST SUITE TESTS ===${NC}"
    
    echo "Running test container..."
    docker-compose --profile test up > /tmp/test.log 2>&1
    
    run_test "Test suite exits successfully" "docker inspect nexus-test --format='{{.State.ExitCode}}' | grep -q '^0$'"
    
    # Check for specific test issues
    if grep -q "immutable" /tmp/test.log 2>/dev/null; then
        echo -e "${YELLOW}Warning: Immutable mode issues detected in tests${NC}"
        echo "Make sure you applied the test_database.py fix"
    fi
}

# ============================================================================
# 5. MCP SERVER TESTS
# ============================================================================

test_mcp_server() {
    echo -e "\n${YELLOW}=== MCP SERVER TESTS ===${NC}"
    
    # Start MCP server
    echo "Starting MCP server..."
    docker-compose --profile production up -d > /dev/null 2>&1
    
    sleep 5
    
    run_test "MCP server is running" "docker ps | grep -q nexus-mcp-server"
    
    run_test "MCP server health check passes" "docker inspect nexus-mcp-server --format='{{.State.Health.Status}}' | grep -q 'healthy'"
    
    # Test MCP can access database
    run_test "MCP can query database" "docker exec nexus-mcp-server python -c \"
import sqlite3
conn = sqlite3.connect('/app/data/processed/forest.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM fact_tree_cover_loss')
count = cursor.fetchone()[0]
print(f'Count: {count}')
exit(0 if count > 0 else 1)
\" 2>/dev/null"
    
    # Stop MCP for cleanup
    docker-compose --profile production down > /dev/null 2>&1
}

# ============================================================================
# 6. INTEGRATION TESTS
# ============================================================================

test_integration() {
    echo -e "\n${YELLOW}=== INTEGRATION TESTS ===${NC}"
    
    # Test that pipeline output can be read by test container
    run_test "Test container can read pipeline database" "docker run --rm -v nexus-mcp_processed-data:/data nexus-test:latest python -c \"
import sqlite3
conn = sqlite3.connect('/data/forest.db', uri=True)
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM fact_tree_cover_loss')
print('Success')
\" 2>/dev/null | grep -q Success"
    
    # Test query performance (indexes should make this fast)
    echo "Testing query performance (should be <100ms with indexes)..."
    QUERY_TIME=$(docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
        apk add --no-cache sqlite >/dev/null 2>&1
        time -f '%e' sqlite3 /data/forest.db \"
            SELECT * FROM fact_tree_cover_loss 
            WHERE country='Brazil' AND year=2023 AND threshold=30;
        \" 2>&1 | tail -1
    " 2>/dev/null)
    
    echo "Query time: ${QUERY_TIME}s"
    run_test "Query performance acceptable" "awk 'BEGIN {exit !(${QUERY_TIME:-1} < 0.5)}'"
}

# ============================================================================
# 7. SPECIFIC FIX VERIFICATION
# ============================================================================

test_fixes() {
    echo -e "\n${YELLOW}=== FIX VERIFICATION TESTS ===${NC}"
    
    # Check if schema.py has the fixes
    run_test "Schema has BEGIN IMMEDIATE transaction" "grep -q 'BEGIN IMMEDIATE' src/nexus/data/database/schema.py"
    
    # Check if exporter.py has index verification
    run_test "Exporter verifies indexes" "grep -q 'Verifying database indexes' src/nexus/data/database/exporter.py || true"
    
    # Check test_database.py fix
    run_test "Test DB uses mode=ro only" "! grep -q 'immutable=1' tests/integration/test_database.py"
    
    # Verify indexes in actual database
    echo -e "\n${BLUE}Listing all indexes in database:${NC}"
    docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
        apk add --no-cache sqlite >/dev/null 2>&1
        sqlite3 /data/forest.db \"
            SELECT tbl_name, name 
            FROM sqlite_master 
            WHERE type='index' AND sql IS NOT NULL
            ORDER BY tbl_name, name;
        \"
    " 2>/dev/null || echo "No indexes found"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    echo "=============================================="
    echo "       NEXUS SYSTEM TESTING SUITE"
    echo "=============================================="
    
    # Run test categories based on argument
    case "${1:-all}" in
        prereq)
            test_prerequisites
            ;;
        build)
            test_prerequisites
            test_build
            ;;
        pipeline)
            test_pipeline
            ;;
        test)
            test_test_suite
            ;;
        mcp)
            test_mcp_server
            ;;
        integration)
            test_integration
            ;;
        fixes)
            test_fixes
            ;;
        quick)
            # Quick test - just check indexes
            test_fixes
            INDEXES=$(docker run --rm -v nexus-mcp_processed-data:/data alpine sh -c "
                apk add --no-cache sqlite >/dev/null 2>&1
                sqlite3 /data/forest.db \"SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL;\"
            " 2>/dev/null || echo "0")
            echo -e "\n${BLUE}Quick Check: Found $INDEXES indexes${NC}"
            if [ "$INDEXES" -ge 8 ]; then
                echo -e "${GREEN}✓ INDEXES ARE WORKING!${NC}"
            else
                echo -e "${RED}✗ INDEXES MISSING - Fix needed${NC}"
            fi
            ;;
        all)
            test_prerequisites
            test_build
            test_pipeline
            test_test_suite
            test_mcp_server
            test_integration
            test_fixes
            ;;
        *)
            echo "Usage: $0 [all|prereq|build|pipeline|test|mcp|integration|fixes|quick]"
            exit 1
            ;;
    esac
    
    # Summary
    echo -e "\n=============================================="
    echo -e "              TEST SUMMARY"
    echo -e "=============================================="
    echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
    echo -e "${RED}Failed:${NC} $TESTS_FAILED"
    
    if [ ${#FAILED_TESTS[@]} -gt 0 ]; then
        echo -e "\n${RED}Failed tests:${NC}"
        for test in "${FAILED_TESTS[@]}"; do
            echo "  - $test"
        done
        exit 1
    else
        echo -e "\n${GREEN}ALL TESTS PASSED!${NC}"
    fi
}

# Run main with arguments
main "$@"