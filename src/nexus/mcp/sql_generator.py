"""
Safe SQL query generator using parameterized queries.
"""
import logging
from typing import Dict, Any, Optional, List, Tuple

from nexus.mcp.models import RoutingDecision, QueryIntent, TableType
from nexus.data.metadata.metadata_manager import metadata_manager

logger = logging.getLogger(__name__)


class SafeSQLGenerator:
    """
    Generate parameterized SQL queries safely.
    
    This generator creates SQL with parameter placeholders to prevent
    SQL injection attacks while maintaining query flexibility.
    """
    
    def __init__(self):
        """Initialize SQL generator with metadata."""
        self.metadata = metadata_manager
    
    def generate(
        self,
        routing: RoutingDecision,
        question: str,
        year: Optional[int] = None,
        country: Optional[str] = None,
        threshold: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """
        Generate parameterized SQL query.
        
        Args:
            routing: Routing decision with tables and intent
            question: Original natural language question
            year: Optional year filter
            country: Optional country filter  
            threshold: Optional threshold filter
            
        Returns:
            Tuple of (SQL query with placeholders, parameter values)
            
        Example:
            sql, params = generator.generate(routing, "Brazil 2023")
            # Returns: ("SELECT * FROM fact WHERE country = ? AND year = ?", ("Brazil", 2023))
        """
        # Route to appropriate generator based on intent
        generators = {
            QueryIntent.PRIMARY_PERCENTAGE: self._generate_primary_percentage,
            QueryIntent.CARBON_INTENSITY: self._generate_carbon_intensity,
            QueryIntent.TREND: self._generate_trend,
            QueryIntent.RANKING: self._generate_ranking,
            QueryIntent.AGGREGATION: self._generate_aggregation,
            QueryIntent.SIMPLE_METRIC: self._generate_simple
        }
        
        generator = generators.get(routing.intent, self._generate_simple)
        
        return generator(
            routing=routing,
            country=country,
            year=year,
            threshold=threshold,
            **kwargs
        )
    
    def _generate_simple(
        self,
        routing: RoutingDecision,
        country: Optional[str] = None,
        year: Optional[int] = None,
        threshold: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """
        Generate simple SELECT query with parameters.
        
        Returns:
            Tuple of (SQL, parameters)
        """
        table = routing.primary_table.value
        
        # Get table metadata to know which columns to select
        table_meta = self.metadata.get_table_metadata(table)
        columns = table_meta.get("schema", {}).get("columns", ["*"])
        
        # Build SELECT clause (safe - column names from metadata)
        select_columns = ", ".join(columns) if columns != ["*"] else "*"
        
        # Build WHERE conditions
        conditions = []
        params = []
        
        if country:
            conditions.append("country = ?")
            params.append(country)
        
        if year:
            conditions.append("year = ?")
            params.append(year)
        
        # Handle threshold based on table type
        if table == "fact_primary_forest":
            # Primary forest always uses threshold 30
            conditions.append("threshold = ?")
            params.append(30)
        elif table == "fact_carbon":
            # Carbon only has 30, 50, 75
            valid = self.metadata.get_valid_thresholds("carbon")
            if threshold and threshold in valid:
                conditions.append("threshold = ?")
                params.append(threshold)
            else:
                # Default to 30 for carbon
                conditions.append("threshold = ?")
                params.append(30)
        elif threshold is not None:
            conditions.append("threshold = ?")
            params.append(threshold)
        elif table == "fact_tree_cover_loss":
            # Default to FAO standard
            conditions.append("threshold = ?")
            params.append(30)
        
        # Build final query
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        
        sql = f"""
            SELECT {select_columns}
            FROM {table}
            {where_clause}
            ORDER BY year DESC
            LIMIT 100
        """
        
        return sql.strip(), tuple(params)
    
    def _generate_primary_percentage(
        self,
        country: Optional[str] = None,
        year: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """Generate query for primary forest percentage."""
        sql = """
            SELECT 
                t.country,
                t.year,
                t.tree_cover_loss_ha,
                p.primary_forest_loss_ha,
                CASE 
                    WHEN t.tree_cover_loss_ha > 0 
                    THEN ROUND((p.primary_forest_loss_ha / t.tree_cover_loss_ha) * 100, 2)
                    ELSE NULL 
                END as primary_percentage
            FROM fact_tree_cover_loss t
            LEFT JOIN fact_primary_forest p
                ON t.country = p.country 
                AND t.year = p.year
            WHERE t.threshold = ?
        """
        
        params = [30]  # Primary forest is always at threshold 30
        
        if country:
            sql += " AND t.country = ?"
            params.append(country)
        
        if year:
            sql += " AND t.year = ?"
            params.append(year)
        
        return sql.strip(), tuple(params)
    
    def _generate_carbon_intensity(
        self,
        country: Optional[str] = None,
        year: Optional[int] = None,
        threshold: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """Generate query for carbon intensity calculation."""
        sql = """
            SELECT 
                t.country,
                t.year,
                t.threshold,
                t.tree_cover_loss_ha,
                c.carbon_emissions_mg_co2e,
                CASE 
                    WHEN t.tree_cover_loss_ha > 0 
                    THEN ROUND(c.carbon_emissions_mg_co2e / t.tree_cover_loss_ha, 2)
                    ELSE NULL 
                END as carbon_per_hectare
            FROM fact_tree_cover_loss t
            INNER JOIN fact_carbon c
                ON t.country = c.country
                AND t.year = c.year
                AND t.threshold = c.threshold
            WHERE 1=1
        """
        
        params = []
        
        if country:
            sql += " AND t.country = ?"
            params.append(country)
        
        if year:
            sql += " AND t.year = ?"
            params.append(year)
        
        # Carbon thresholds: 30, 50, 75 only
        valid_thresholds = self.metadata.get_valid_thresholds("carbon")
        if threshold and threshold in valid_thresholds:
            sql += " AND t.threshold = ?"
            params.append(threshold)
        else:
            sql += " AND t.threshold = ?"
            params.append(30)  # Default
        
        return sql.strip(), tuple(params)
    
    def _generate_trend(
        self,
        routing: RoutingDecision,
        country: Optional[str] = None,
        threshold: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """Generate trend analysis query."""
        sql = """
            SELECT 
                year,
                SUM(tree_cover_loss_ha) as total_loss_ha
            FROM fact_tree_cover_loss
            WHERE threshold = ?
        """
        
        threshold = threshold or 30  # Default to FAO standard
        params = [threshold]
        
        if country:
            sql += " AND country = ?"
            params.append(country)
        
        sql += " GROUP BY year ORDER BY year"
        
        return sql.strip(), tuple(params)
    
    def _generate_ranking(
        self,
        routing: RoutingDecision,
        threshold: Optional[int] = None,
        start_year: int = 2020,
        end_year: int = 2024,
        limit: int = 10,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """Generate ranking query."""
        sql = """
            SELECT 
                country,
                SUM(tree_cover_loss_ha) as total_loss_ha
            FROM fact_tree_cover_loss
            WHERE threshold = ?
                AND year BETWEEN ? AND ?
            GROUP BY country
            ORDER BY total_loss_ha DESC
            LIMIT ?
        """
        
        threshold = threshold or 30
        params = (threshold, start_year, end_year, limit)
        
        return sql, params
    
    def _generate_aggregation(
        self,
        routing: RoutingDecision,
        country: Optional[str] = None,
        year: Optional[int] = None,
        threshold: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Tuple]:
        """Generate aggregation query."""
        table = routing.primary_table.value
        
        # Get value column from metadata
        table_meta = self.metadata.get_table_metadata(table)
        value_column = table_meta.get("schema", {}).get("value_column", "value")
        
        sql = f"""
            SELECT 
                COUNT(*) as record_count,
                SUM({value_column}) as total_value,
                AVG({value_column}) as average_value,
                MAX({value_column}) as max_value,
                MIN({value_column}) as min_value
            FROM {table}
            WHERE {value_column} IS NOT NULL
        """
        
        params = []
        
        if country:
            sql += " AND country = ?"
            params.append(country)
        
        if year:
            sql += " AND year = ?"
            params.append(year)
        
        # Handle threshold
        if table == "fact_primary_forest":
            sql += " AND threshold = ?"
            params.append(30)
        elif table == "fact_carbon" and threshold in [30, 50, 75]:
            sql += " AND threshold = ?"
            params.append(threshold)
        elif threshold:
            sql += " AND threshold = ?"
            params.append(threshold)
        else:
            sql += " AND threshold = ?"
            params.append(30)
        
        return sql.strip(), tuple(params)