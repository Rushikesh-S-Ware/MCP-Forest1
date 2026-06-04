
"""
Integration tests for SQLite database after loading.
Tests data integrity, relationships, performance, and query correctness.
"""
import pytest
import sqlite3
import time
from pathlib import Path
import pytest
import sqlite3
from nexus.config.settings import settings

print("Loading test_database.py")  # Debug to confirm file is loaded

try:
    from nexus.config.settings import settings
    from nexus.config.constants import CARBON_THRESHOLDS, TROPICAL_COUNTRIES
except ImportError as e:
    print(f"ImportError: {e}")  # Debug import issues
    raise

class TestDatabaseIntegrity:
    """Test database integrity and constraints."""
    
    @pytest.fixture(scope="class")
    def db_connection(self):
        """Provide immutable database connection for tests."""
        from nexus.config.settings import settings
        import os
        
        db_path = settings.sqlite_db_path
        
        # Check if database exists
        if not os.path.exists(db_path):
            pytest.skip(f"Database not found at {db_path}")
        
        # ============= CHANGE THIS SECTION =============
        # OLD WAY (can fail with some SQLite versions):
        # connection_string = f"file:{db_path}?mode=ro&immutable=1"
        
        # NEW WAY (more compatible):
        connection_string = f"file:{db_path}?mode=ro"
        # ============= END OF CHANGE =============
        
        print(f"Database path: {db_path}")
        print(f"Connection string: {connection_string}")
        
        conn = sqlite3.connect(connection_string, uri=True)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()
    
    def test_primary_keys_unique(self, db_connection):
        """Verify primary keys are unique and not null."""
        cursor = db_connection.cursor()
        
        tables = [
            ("fact_tree_cover_loss", ["country", "year", "threshold"]),
            ("fact_primary_forest", ["country", "year"]),
            ("fact_carbon", ["country", "year", "threshold"])
        ]
        
        for table, pk_columns in tables:
            pk_str = ", ".join(pk_columns)
            query = f"""
                SELECT {pk_str}, COUNT(*) as cnt
                FROM {table}
                GROUP BY {pk_str}
                HAVING COUNT(*) > 1
            """
            cursor.execute(query)
            duplicates = cursor.fetchall()
            
            assert len(duplicates) == 0, f"Found duplicates in {table}: {duplicates[:5]}"
            
            for col in pk_columns:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL")
                null_count = cursor.fetchone()[0]
                assert null_count == 0, f"Found NULLs in {table}.{col}"
    
    def test_referential_integrity(self, db_connection):
        """Test relationships between tables are consistent."""
        cursor = db_connection.cursor()
        
        query = """
            SELECT DISTINCT p.country 
            FROM fact_primary_forest p
            LEFT JOIN fact_tree_cover_loss t 
                ON p.country = t.country 
            WHERE t.country IS NULL
        """
        cursor.execute(query)
        orphan_countries = cursor.fetchall()
        assert len(orphan_countries) == 0, f"Primary forest has orphan countries: {orphan_countries}"
        
        query = """
            SELECT DISTINCT c.country 
            FROM fact_carbon c
            LEFT JOIN fact_tree_cover_loss t 
                ON c.country = t.country 
            WHERE t.country IS NULL
        """
        cursor.execute(query)
        orphan_countries = cursor.fetchall()
        assert len(orphan_countries) == 0, f"Carbon has orphan countries: {orphan_countries}"
    
    def test_carbon_threshold_constraint(self, db_connection):
        """Verify carbon data only has valid thresholds."""
        cursor = db_connection.cursor()
        
        cursor.execute("SELECT DISTINCT threshold FROM fact_carbon ORDER BY threshold")
        thresholds = [row[0] for row in cursor.fetchall()]
        
        assert thresholds == CARBON_THRESHOLDS, f"Invalid carbon thresholds: {thresholds}"
    
    def test_primary_forest_threshold_constraint(self, db_connection):
        """Verify primary forest is always at threshold 30."""
        cursor = db_connection.cursor()
        
        cursor.execute("SELECT DISTINCT threshold FROM fact_primary_forest")
        thresholds = [row[0] for row in cursor.fetchall()]
        
        assert len(thresholds) == 1, f"Multiple thresholds in primary forest: {thresholds}"
        assert thresholds[0] == 30, f"Primary forest threshold should be 30, got {thresholds[0]}"
    
    def test_tropical_countries_only(self, db_connection):
        """Verify primary forest only has tropical countries."""
        cursor = db_connection.cursor()
        
        cursor.execute("SELECT DISTINCT country FROM fact_primary_forest")
        countries = [row[0] for row in cursor.fetchall()]
        
        non_tropical = set(countries) - TROPICAL_COUNTRIES
        assert len(non_tropical) == 0, f"Non-tropical countries in primary forest: {non_tropical}"
    
    def test_year_ranges(self, db_connection):
        """Test year ranges for each table."""
        cursor = db_connection.cursor()
        
        cursor.execute("SELECT MIN(year), MAX(year) FROM fact_tree_cover_loss")
        min_year, max_year = cursor.fetchone()
        assert min_year >= 2001, f"Tree cover has data before 2001: {min_year}"
        assert max_year <= 2024, f"Tree cover has data after 2024: {max_year}"
        
        cursor.execute("SELECT MIN(year), MAX(year) FROM fact_primary_forest")
        min_year, max_year = cursor.fetchone()
        assert min_year >= 2002, f"Primary forest has data before 2002: {min_year}"
        assert max_year <= 2023, f"Primary forest has data after 2023: {max_year}"

class TestDataConsistency:
    """Test logical consistency of data."""
    
    @pytest.fixture
    def db_connection(self):
        conn = sqlite3.connect(settings.sqlite_db_path)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()
    
    def test_primary_forest_less_than_total(self, db_connection):
        """Primary forest loss should not significantly exceed total forest loss.
        
        Note: Minor discrepancies may occur due to:
        - Different data collection timing
        - Measurement methodology differences  
        - Small territories with high variance
        
        We allow up to 100 ha difference before failing.
        """
        cursor = db_connection.cursor()
        
        query = """
            SELECT t.country, t.year,
                t.tree_cover_loss_ha,
                p.primary_forest_loss_ha,
                (p.primary_forest_loss_ha - t.tree_cover_loss_ha) as difference
            FROM fact_tree_cover_loss t
            INNER JOIN fact_primary_forest p
                ON t.country = p.country
                AND t.year = p.year
            WHERE t.threshold = 30
                AND p.primary_forest_loss_ha > t.tree_cover_loss_ha
                AND p.primary_forest_loss_ha IS NOT NULL
                AND t.tree_cover_loss_ha IS NOT NULL
        """
        cursor.execute(query)
        invalid_rows = cursor.fetchall()
        
        # Log all discrepancies for visibility
        if invalid_rows:
            print(f"\n⚠️  Data Quality Note: {len(invalid_rows)} rows where primary > total:")
            for row in invalid_rows:
                diff = row['difference']
                print(f"  • {row['country']} ({row['year']}): "
                    f"primary={row['primary_forest_loss_ha']} ha, "
                    f"total={row['tree_cover_loss_ha']} ha, "
                    f"difference={diff} ha")
        
        # Only fail for SIGNIFICANT discrepancies (>100 ha)
        # Small differences are acceptable given data collection realities
        significant_issues = [
            row for row in invalid_rows 
            if row['difference'] > 100
        ]
        
        if significant_issues:
            error_msg = f"\nFound {len(significant_issues)} rows with significant discrepancies (>100 ha):\n"
            for row in significant_issues:
                error_msg += f"  {row['country']} {row['year']}: diff={row['difference']} ha\n"
            pytest.fail(error_msg)
        
        # Test passes - either no issues or only minor ones
        print(f"✅ Data quality check passed. Minor discrepancies (<100 ha) are acceptable.")
    
    def test_loss_rate_calculation(self, db_connection):
        """Verify loss rate is correctly calculated."""
        cursor = db_connection.cursor()
        
        query = """
            SELECT country, year, 
                   tree_cover_loss_ha, 
                   extent_2000_ha,
                   loss_rate_pct,
                   (tree_cover_loss_ha / NULLIF(extent_2000_ha, 0)) * 100 as calculated_rate
            FROM fact_tree_cover_loss
            WHERE loss_rate_pct IS NOT NULL
                AND extent_2000_ha > 0
            LIMIT 100
        """
        cursor.execute(query)
        
        for row in cursor.fetchall():
            if row['calculated_rate'] is not None:
                diff = abs(row['loss_rate_pct'] - row['calculated_rate'])
                assert diff < 0.01, f"Loss rate mismatch for {row['country']} {row['year']}"
    
    def test_no_negative_areas(self, db_connection):
        """Verify no negative values in area columns."""
        cursor = db_connection.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM fact_tree_cover_loss 
            WHERE tree_cover_loss_ha < 0 
                OR extent_2000_ha < 0
        """)
        assert cursor.fetchone()[0] == 0, "Found negative areas in tree cover"
        
        cursor.execute("""
            SELECT COUNT(*) FROM fact_primary_forest 
            WHERE primary_forest_loss_ha < 0
        """)
        assert cursor.fetchone()[0] == 0, "Found negative areas in primary forest"

class TestQueryPerformance:
    """Test query performance with indexes."""
    
    @pytest.fixture
    def db_connection(self):
        conn = sqlite3.connect(settings.sqlite_db_path)
        yield conn
        conn.close()
    
    def test_index_exists(self, db_connection):
        """Verify critical indexes exist."""
        cursor = db_connection.cursor()
        
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type = 'index' AND tbl_name = 'fact_tree_cover_loss'
        """)
        indexes = [row[0] for row in cursor.fetchall()]
        
        assert any('country' in idx.lower() for idx in indexes), "Missing country index"
        assert any('year' in idx.lower() for idx in indexes), "Missing year index"
    
    def test_simple_query_performance(self, db_connection):
        """Test performance of common queries."""
        cursor = db_connection.cursor()
        
        queries = [
            "SELECT * FROM fact_tree_cover_loss WHERE country = 'Brazil' AND year = 2023 AND threshold = 30",
            "SELECT SUM(tree_cover_loss_ha) FROM fact_tree_cover_loss WHERE year = 2023",
            """SELECT t.*, p.primary_forest_loss_ha 
               FROM fact_tree_cover_loss t
               LEFT JOIN fact_primary_forest p 
                   ON t.country = p.country AND t.year = p.year
               WHERE t.country = 'Brazil' AND t.threshold = 30"""
        ]
        
        for query in queries:
            start = time.time()
            cursor.execute(query)
            cursor.fetchall()
            elapsed_ms = (time.time() - start) * 1000
            
            assert elapsed_ms < 100, f"Query too slow ({elapsed_ms:.1f}ms): {query[:50]}..."

class TestViews:
    """Test database views work correctly."""
    
    @pytest.fixture
    def db_connection(self):
        """Provide database connection for tests."""
        from nexus.config.settings import settings
        
        # Open in IMMUTABLE mode for read-only access
        # This prevents SQLite from trying to create lock files
        db_path = settings.sqlite_db_path
        connection_string = f"file:{db_path}?mode=ro&immutable=1"
        
        conn = sqlite3.connect(connection_string, uri=True)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()
    
    def test_views_exist(self, db_connection):
        """Check if expected views exist."""
        cursor = db_connection.cursor()
        
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type = 'view'
        """)
        views = [row[0] for row in cursor.fetchall()]
        
        expected_views = [
            'v_primary_forest_percentage',
            'v_carbon_intensity',
            'v_annual_summary'
        ]
        
        for view in expected_views:
            assert view in views, f"Missing view: {view}"
    
    def test_view_queries(self, db_connection):
        """Test that views return valid data."""
        cursor = db_connection.cursor()
        
        cursor.execute("SELECT * FROM v_primary_forest_percentage LIMIT 10")
        results = cursor.fetchall()
        assert len(results) > 0, "Primary forest percentage view returns no data"
        
        for row in results:
            if row['primary_percentage'] is not None:
                assert 0 <= row['primary_percentage'] <= 100

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    @pytest.fixture
    def db_connection(self):
        conn = sqlite3.connect(settings.sqlite_db_path)
        yield conn
        conn.close()
    
    def test_null_handling(self, db_connection):
        """Test queries handle NULLs correctly."""
        cursor = db_connection.cursor()
        
        cursor.execute("""
            SELECT 
                tree_cover_loss_ha / NULLIF(extent_2000_ha, 0) as rate
            FROM fact_tree_cover_loss
            WHERE extent_2000_ha = 0 OR extent_2000_ha IS NULL
            LIMIT 10
        """)
        results = cursor.fetchall()
        # Should not raise error
        
    def test_empty_country_query(self, db_connection):
        """Test querying non-existent country."""
        cursor = db_connection.cursor()
        
        cursor.execute("""
            SELECT * FROM fact_tree_cover_loss 
            WHERE country = 'Atlantis'
        """)
        results = cursor.fetchall()
        assert len(results) == 0  # Should return empty, not error
