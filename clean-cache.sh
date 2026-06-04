#!/bin/bash
# Clean Python and build caches

echo "🧹 Cleaning Python and build caches..."

# Python cache
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type f -name "*.pyo" -delete 2>/dev/null || true
find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Build artifacts
rm -rf build/ dist/ .eggs/ 2>/dev/null || true

# Test artifacts
rm -rf .pytest_cache/ .coverage htmlcov/ test-reports/ 2>/dev/null || true

# UV cache
rm -rf .venv/ 2>/dev/null || true

echo "✅ Cache cleanup complete!"
