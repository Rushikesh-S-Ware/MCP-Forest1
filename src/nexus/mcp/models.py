"""
Pydantic models for MCP server request/response validation.
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum

from nexus.config.constants import ALL_THRESHOLDS, TREE_COVER_YEARS


class QueryIntent(str, Enum):
    """Types of query intents."""
    SIMPLE_METRIC = "simple_metric"
    COMPARISON = "comparison"
    TREND = "trend"
    RANKING = "ranking"
    PRIMARY_PERCENTAGE = "primary_percentage"
    CARBON_INTENSITY = "carbon_intensity"
    AGGREGATION = "aggregation"


class TableType(str, Enum):
    """Available fact tables."""
    TREE_COVER = "fact_tree_cover_loss"
    PRIMARY_FOREST = "fact_primary_forest"
    CARBON = "fact_carbon"


class QueryRequest(BaseModel):
    """Natural language query request."""
    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural language question about forest data"
    )
    context: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Optional context from previous queries"
    )
    year: Optional[int] = Field(
        None,
        ge=min(TREE_COVER_YEARS),
        le=max(TREE_COVER_YEARS),
        description="Specific year to filter"
    )
    country: Optional[str] = Field(
        None,
        max_length=100,
        description="Country name to filter"
    )
    threshold: Optional[int] = Field(
        default=30,
        description="Tree canopy density threshold"
    )
    
    @validator('threshold')
    def validate_threshold(cls, v):
        if v not in ALL_THRESHOLDS:
            raise ValueError(f"Threshold must be one of {ALL_THRESHOLDS}")
        return v
        
    class Config:
     json_schema_extra = { 
            "example": {
                "question": "What was Brazil's forest loss in 2023?",
                "year": 2023,
                "country": "Brazil",
                "threshold": 30
            }
        }

class RoutingDecision(BaseModel):
    """Query routing decision."""
    tables: List[TableType] = Field(
        ..., description="Tables required for the query"
    )
    primary_table: TableType = Field(
        ..., description="Primary table to query"
    )
    requires_join: bool = Field(
        default=False, description="Whether tables need to be joined"
    )
    join_conditions: Optional[List[str]] = Field(
        None, description="SQL join conditions if required"
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict, description="Filters to apply"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in routing decision"
    )
    intent: QueryIntent = Field(
        ..., description="Identified query intent"
    )


class QueryResponse(BaseModel):
    """Query response with metadata."""
    answer: str = Field(
        ..., description="Natural language answer to the question"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in the answer"
    )
    sql_executed: str = Field(
        ..., description="SQL query that was executed"
    )
    source: str = Field(
        default="Hansen et al. 2024, Global Forest Watch",
        description="Data source citation"
    )
    tables_used: List[str] = Field(
        ..., description="Database tables queried"
    )
    rows_returned: int = Field(
        ..., ge=0, description="Number of rows in result set"
    )
    processing_time_ms: float = Field(
        ..., ge=0, description="Query processing time in milliseconds"
    )
    data: Optional[List[Dict[str, Any]]] = Field(
        None, description="Raw query results if requested"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "answer": "Brazil's forest loss in 2023 was 1,234,567 hectares.",
                "confidence": 0.95,
                "sql_executed": "SELECT tree_cover_loss_ha FROM fact_tree_cover_loss WHERE country='Brazil' AND year=2023 AND threshold=30",
                "source": "Hansen et al. 2024, Global Forest Watch",
                "tables_used": ["fact_tree_cover_loss"],
                "rows_returned": 1,
                "processing_time_ms": 15.3,
                "metadata": {
                    "routing": {"intent": "simple_metric"},
                    "parameters": {"year": 2023, "country": "Brazil", "threshold": 30}
                }
            }
        }


class MCPTool(BaseModel):
    """MCP tool definition."""
    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    parameters: Dict[str, Any] = Field(..., description="Tool parameters schema")
    
    class Config:
        schema_extra = {
            "example": {
                "name": "query_forest_data",
                "description": "Query forest loss, carbon, and primary forest data",
                "parameters": {
                    "question": {"type": "string", "required": True},
                    "year": {"type": "integer", "min": 2001, "max": 2024},
                    "country": {"type": "string"},
                    "threshold": {"type": "integer", "enum": [0, 10, 15, 20, 25, 30, 50, 75]}
                }
            }
        }


class HealthStatus(BaseModel):
    """Health check response."""
    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        ..., description="System health status"
    )
    database: Literal["connected", "disconnected", "error"] = Field(
        ..., description="Database connection status"
    )
    tables: Dict[str, int] = Field(
        ..., description="Row counts for each table"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Health check timestamp"
    )
    version: str = Field(
        default="1.0.0", description="API version"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "status": "healthy",
                "database": "connected",
                "tables": {
                    "fact_tree_cover_loss": 31680,
                    "fact_primary_forest": 1725,
                    "fact_carbon": 11880
                },
                "timestamp": "2025-01-20T12:00:00",
                "version": "1.0.0"
            }
        }


class StatisticsRequest(BaseModel):
    """Request for statistical summaries."""
    metric: Literal["loss", "carbon", "primary"] = Field(
        ..., description="Metric to calculate statistics for"
    )
    aggregation: Literal["sum", "avg", "max", "min", "count"] = Field(
        ..., description="Type of aggregation"
    )
    group_by: Optional[List[Literal["country", "year", "threshold"]]] = Field(
        None, description="Columns to group by"
    )
    filters: Optional[Dict[str, Any]] = Field(
        None, description="Optional filters to apply"
    )


class StatisticsResponse(BaseModel):
    """Statistical summary response."""
    metric: str = Field(..., description="Metric analyzed")
    aggregation: str = Field(..., description="Aggregation performed")
    result: Any = Field(..., description="Aggregation result")
    groups: Optional[List[Dict[str, Any]]] = Field(
        None, description="Grouped results if applicable"
    )
    sql_executed: str = Field(..., description="SQL query executed")
    

class ErrorResponse(BaseModel):
    """Error response model."""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Detailed error information")
    suggestions: Optional[List[str]] = Field(
        None, description="Suggested corrections or alternatives"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Error timestamp"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "error": "Country not found",
                "detail": "Country 'Atlantis' not found in the database",
                "suggestions": ["Did you mean 'Atlantic Ocean'?", "Try 'Australia' or 'Austria'"],
                "timestamp": "2025-01-20T12:00:00"
            }
        }


class QueryExample(BaseModel):
    """Example query for documentation."""
    question: str = Field(..., description="Example question")
    category: str = Field(..., description="Query category")
    expected_tables: List[str] = Field(..., description="Tables that will be queried")
    description: str = Field(..., description="What this query does")
    
    class Config:
        schema_extra = {
            "example": {
                "question": "What percentage of Brazil's forest loss is primary forest?",
                "category": "Primary Forest Analysis",
                "expected_tables": ["fact_tree_cover_loss", "fact_primary_forest"],
                "description": "Calculates the proportion of primary forest within total forest loss"
            }
        }