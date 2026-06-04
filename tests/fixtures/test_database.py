"""
Test database fixtures for unit and integration tests.
"""
import sqlite3
from pathlib import Path
from typing import Generator
import pytest

from nexus.data.database.schema import SchemaManager


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory) -> Path:
    """Create a temporary database path for testing."""
    return tmp_path_factory.mktemp("data") / "test_forest.db"


@pytest.fixture(scope="function")
def in_memory_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Create an in-memory SQLite database for fast unit tests.
    
    Yields:
        Database connection
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Create schema
    schema_manager = SchemaManager()
    schema_manager.create_all_tables(conn)
    
    # Insert minimal test data
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO fact_tree_cover_loss 
        (country, year, threshold, tree_cover_loss_ha)
        VALUES (?, ?, ?, ?)
    """, ("Brazil", 2023, 30, 1234567.0))
    
    conn.commit()
    
    yield conn
    
    conn.close()


@pytest.fixture(scope="module")
def integration_db(test_db_path: Path) -> Generator[Path, None, None]:
    """
    Create a test database file for integration tests.
    
    Yields:
        Path to test database
    """
    # Create database with full schema
    conn = sqlite3.connect(test_db_path)
    
    schema_manager = SchemaManager(test_db_path)
    schema_manager.create_all_tables(conn)
    
    # Load more comprehensive test data
    _load_test_data(conn)
    
    conn.close()
    
    yield test_db_path
    
    # Cleanup
    if test_db_path.exists():
        test_db_path.unlink()


def _load_test_data(conn: sqlite3.Connection):
    """Load test data into database."""
    cursor = conn.cursor()
    
    # Test data for multiple countries and years
    test_data = [
        ("Brazil", 2021, 30, 1000000.0),
        ("Brazil", 2022, 30, 1100000.0),
        ("Brazil", 2023, 30, 1234567.0),
        ("Indonesia", 2023, 30, 890000.0),
        ("Peru", 2023, 30, 456000.0),
    ]
    
    cursor.executemany("""
        INSERT INTO fact_tree_cover_loss 
        (country, year, threshold, tree_cover_loss_ha)
        VALUES (?, ?, ?, ?)
    """, test_data)
    
    conn.commit()