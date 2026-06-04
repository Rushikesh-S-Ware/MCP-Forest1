#!/bin/bash
set -e

echo "=================================================="
echo "  Nexus Data Pipeline - Forest Data Processor"
echo "=================================================="

# Check if Excel file exists
if [ ! -f "/app/data/raw/global_05212025.xlsx" ]; then
    echo "❌ ERROR: Excel file not found at /app/data/raw/"
    echo ""
    echo "Please mount your Excel file:"
    echo "  docker run -v /path/to/data.xlsx:/app/data/raw/global_05212025.xlsx ..."
    exit 1
fi

# Display file info
echo "📊 Input Excel file found:"
ls -lh /app/data/raw/global_05212025.xlsx

echo ""
echo "🔄 Starting data processing pipeline..."
echo ""

# Run the pipeline with all arguments passed to the container
uv run python -m nexus.main "$@"

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=================================================="
    echo "✅ Pipeline completed successfully!"
    echo "=================================================="
    echo ""
    echo "📁 Output database: /app/data/processed/forest.db"
    ls -lh /app/data/processed/forest.db
    echo ""
    echo "📊 Database statistics:"
    sqlite3 /app/data/processed/forest.db "SELECT 'Tree Cover Loss: ', COUNT(*) FROM fact_tree_cover_loss UNION ALL SELECT 'Primary Forest: ', COUNT(*) FROM fact_primary_forest UNION ALL SELECT 'Carbon Data: ', COUNT(*) FROM fact_carbon;"
else
    echo ""
    echo "=================================================="
    echo "❌ Pipeline failed with exit code $EXIT_CODE"
    echo "=================================================="
    exit $EXIT_CODE
fi