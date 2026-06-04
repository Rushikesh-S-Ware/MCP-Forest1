"""
MCP Server for Global Forest Watch Data Analysis
Provides comprehensive forest data query tools with ClimateGPT integration
"""
import asyncio
import json
import sys
import logging
import os
import base64
from pathlib import Path  # ← ADD THIS LINE
from typing import Any, Dict, List, Optional
import sqlite3

import httpx
from mcp.server import Server
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

# Import existing components
from nexus.mcp.router import QueryRouter
from nexus.mcp.sql_generator import SafeSQLGenerator
from nexus.config.settings import settings
from nexus.data.metadata.metadata_manager import metadata_manager
from nexus.mcp import llm_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# LLM narrative-analysis configuration now lives in nexus.mcp.llm_client
# (provider-agnostic, OpenAI-compatible). Defaults to Groq + Llama 3.3 70B for
# free/live use; supports local Ollama and the legacy ClimateGPT endpoint via
# environment variables. See llm_client.py for the supported setups.
DATABASE_PATH = os.getenv("DATABASE_PATH", str(settings.sqlite_db_path))

# Simple QueryExecutor
class QueryExecutor:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
    
    def execute_query(self, sql: str, params: tuple = None) -> List[Dict]:
        """Execute SQL query and return results"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return []

# Initialize components
query_executor = QueryExecutor()
query_router = QueryRouter()
sql_generator = SafeSQLGenerator()

# Create MCP server
app = Server("forest-data-analyzer")

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def call_climategpt(question: str, data: List[Dict], context: str = "") -> str:
    """
    Generate a short business-facing narrative for query results.

    Despite the legacy name, this now delegates to the provider-agnostic
    llm_client (Groq/Llama by default, configurable via env). Kept named
    `call_climategpt` so all existing tool call sites work unchanged.
    Returns "" on any failure so structured data is always returned.
    """
    if not data:
        return ""

    # Format data concisely
    data_summary = format_data_for_climategpt(data)

    system_prompt = """You are a sustainability consultant analyzing Global Forest Watch deforestation data for corporate executives and business leaders.

Focus on:
- Business risks and opportunities (supply chain, reputation, regulatory)
- Financial implications and materiality assessment
- ESG reporting requirements (TCFD, CDP, GRI, CSRD)
- Market trends and competitive positioning
- Stakeholder expectations (investors, customers, employees)
- Actionable business strategies with ROI considerations

Provide business-focused insights linking environmental data to financial performance. Format numbers with commas. Keep under 200 words."""

    user_prompt = f"""Question: {question}

Data:
{data_summary}

{context}

Provide brief climate analysis highlighting key findings and environmental implications."""

    return await llm_client.chat_completion(system_prompt, user_prompt)

def format_data_for_climategpt(data: List[Dict]) -> str:
    """Format data for ClimateGPT - concise summary"""
    if len(data) == 1:
        row = data[0]
        lines = []
        for k, v in row.items():
            if isinstance(v, (int, float)) and v is not None:
                lines.append(f"{k}: {v:,.0f}")
            elif v is not None:
                lines.append(f"{k}: {v}")
        return "\n".join(lines)
    
    # Multiple rows - show first 10
    lines = []
    for i, row in enumerate(data[:10], 1):
        items = []
        for k, v in row.items():
            if isinstance(v, (int, float)) and v is not None:
                items.append(f"{k}={v:,.0f}")
            elif v is not None:
                items.append(f"{k}={v}")
        lines.append(f"{i}. " + ", ".join(items))
    
    if len(data) > 10:
        lines.append(f"... ({len(data) - 10} more rows)")
    
    return "\n".join(lines)

def format_number(value) -> str:
    """Format numbers with commas"""
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:,.0f}"
    return str(value)

def get_latest_year() -> int:
    """Get latest year from database"""
    sql = "SELECT MAX(year) as max_year FROM fact_tree_cover_loss"
    result = query_executor.execute_query(sql)
    return result[0]['max_year'] if result else 2024

# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

@app.list_tools()
async def list_tools() -> List[types.Tool]:
    """List all available tools with detailed descriptions"""
    return [
        
        # ===== TOOL 1: Tree Cover Loss Queries =====
        types.Tool(
            name="query_tree_cover_loss",
            description="""Query tree cover loss data for specific country/year combinations.

WHAT IT DOES:
- Lookup forest loss for a single country in a specific year
- Support different canopy density thresholds (0%, 10%, 30%, 50%, 75%)
- Return loss amount, extent, and loss rate
- Default threshold is 30% (FAO standard)

WHAT IT DOESN'T DO:
- Multi-year trends (use analyze_trend instead)
- Multiple countries at once (use compare_countries instead)
- Primary forest data (use query_primary_forest instead)
- Carbon data (use query_carbon_data instead)

EXAMPLES:
✓ "What was Brazil's tree cover loss in 2023?"
✓ "What is Russia's dense forest (75%) loss in 2022?"
✓ "What is India's tree cover loss at 10% threshold in 2021?"
✓ "What is the tree cover extent in 2000 for China?"
✗ "Show Brazil's trend from 2010-2023" (use analyze_trend)
✗ "Compare Brazil vs Indonesia" (use compare_countries)

PARAMETERS:
- country (required): Country name
- year (optional): Year (2001-2024), defaults to latest
- threshold (optional): Canopy density % (0,10,15,20,25,30,50,75), defaults to 30
- include_extent (optional): Include 2000/2010 extent data, defaults to false""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Country name (e.g., 'Brazil', 'Indonesia', 'Russia')"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year (optional, defaults to latest year)"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [0, 10, 15, 20, 25, 30, 50, 75],
                        "description": "Canopy density threshold % (optional, defaults to 30)"
                    },
                    "include_extent": {
                        "type": "boolean",
                        "description": "Include forest extent data (optional, defaults to false)"
                    }
                },
                "required": ["country"]
            }
        ),
        
        # ===== TOOL 2: Primary Forest Queries =====
        types.Tool(
            name="query_primary_forest",
            description="""Query primary (virgin) forest loss data for tropical countries.

WHAT IT DOES:
- Lookup primary forest loss for tropical countries only
- Single country/year queries
- Always uses 30% threshold (primary forest standard)
- Returns loss amount and tropical status

WHAT IT DOESN'T DO:
- Non-tropical countries (they have no primary forest data)
- Multi-year trends (use analyze_trend with metric='primary')
- Total tree cover loss (use query_tree_cover_loss instead)
- Primary forest as percentage (use calculate_primary_share instead)

IMPORTANT: Primary forest data only exists for 75 tropical countries.

EXAMPLES:
✓ "How much primary forest did Indonesia lose in 2020?"
✓ "What is Brazil's primary forest loss in the latest year?"
✓ "What is Papua New Guinea's primary forest loss in 2023?"
✓ "What is Gabon's virgin forest loss in 2022?"
✗ "What is Canada's primary forest loss?" (Canada is not tropical)
✗ "Primary forest trend for Brazil" (use analyze_trend)
✗ "What % of Brazil's loss is primary?" (use calculate_primary_share)

PARAMETERS:
- country (required): Tropical country name
- year (optional): Year (2002-2024), defaults to latest""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Tropical country name (e.g., 'Brazil', 'Indonesia', 'Peru')"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2002,
                        "maximum": 2024,
                        "description": "Year (optional, defaults to latest)"
                    }
                },
                "required": ["country"]
            }
        ),
        
        # ===== TOOL 3: Carbon Data Queries =====
        types.Tool(
            name="query_carbon_data",
            description="""Query carbon emissions and flux data from deforestation.

WHAT IT DOES:
- Lookup carbon emissions for specific country/year
- Return emissions (Mg CO2e), removals, net flux, and carbon density
- Identify if country is carbon sink (negative flux) or source (positive flux)
- Support thresholds 30%, 50%, 75% only (carbon data limitation)

WHAT IT DOESN'T DO:
- Provide data for 0%, 10%, 15%, 20%, 25% thresholds (not available)
- Multi-year trends (use analyze_trend with metric='carbon')
- Carbon per hectare calculations (use calculate_carbon_intensity instead)
- Global aggregations (use aggregate_global instead)

EXAMPLES:
✓ "How much carbon was emitted from deforestation in Peru in 2021?"
✓ "What is Brazil's carbon density in the latest year?"
✓ "Is Indonesia a carbon sink or source in 2023?"
✓ "What are Russia's carbon removals in 2022?"
✗ "Global carbon emissions in 2020" (use aggregate_global)
✗ "Carbon trend for Brazil 2010-2023" (use analyze_trend)
✗ "Carbon per hectare for Indonesia" (use calculate_carbon_intensity)

PARAMETERS:
- country (required): Country name
- year (optional): Year (2001-2024), defaults to latest
- threshold (optional): 30, 50, or 75 only, defaults to 30""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Country name"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year (optional, defaults to latest)"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [30, 50, 75],
                        "description": "Threshold (optional, defaults to 30)"
                    }
                },
                "required": ["country"]
            }
        ),
        
        # ===== TOOL 4: Trend Analysis =====
        types.Tool(
            name="analyze_trend",
            description="""Analyze time series trends for forest loss, primary forest, or carbon data.

WHAT IT DOES:
- Multi-year time series for single country or globally
- Support metrics: tree cover loss, primary forest, carbon emissions, carbon removals
- Calculate year-over-year changes and overall trend direction
- Optionally include moving averages

WHAT IT DOESN'T DO:
- Single year lookups (use query_tree_cover_loss, query_primary_forest, or query_carbon_data)
- Multiple countries simultaneously (use compare_countries instead)
- Cross-metric analysis (use appropriate multi-table tools)

EXAMPLES:
✓ "What is the trend of tree cover loss for Brazil from 2015 to 2024?"
✓ "Show global deforestation trend from 2001 to 2024"
✓ "What is Peru's primary forest loss trend from 2010 to 2024?"
✓ "Indonesia's carbon emissions trend 2010-2023"
✓ "Show Russia's carbon removals trend since 2015"
✗ "Brazil's loss in 2023" (use query_tree_cover_loss)
✗ "Compare Brazil vs Indonesia trends" (use compare_countries)

PARAMETERS:
- metric (required): 'loss', 'primary', 'carbon_emissions', or 'carbon_removals'
- country (optional): Country name, omit for global
- start_year (optional): Start year, defaults to 2001
- end_year (optional): End year, defaults to latest
- threshold (optional): Threshold %, defaults to 30""",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["loss", "primary", "carbon_emissions", "carbon_removals"],
                        "description": "Metric to analyze"
                    },
                    "country": {
                        "type": "string",
                        "description": "Country name (optional, omit for global trend)"
                    },
                    "start_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Start year (optional, defaults to 2001)"
                    },
                    "end_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "End year (optional, defaults to latest)"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [0, 10, 15, 20, 25, 30, 50, 75],
                        "description": "Threshold (optional, defaults to 30)"
                    }
                },
                "required": ["metric"]
            }
        ),
        
        # ===== TOOL 5: Country Comparison =====
        types.Tool(
            name="compare_countries",
            description="""Compare forest metrics across 2-10 countries side-by-side.

WHAT IT DOES:
- Compare 2-10 countries on same metric(s)
- Support single year or multi-year comparison
- Compare loss, primary forest, carbon, or multiple metrics
- Show relative rankings and differences

WHAT IT DOESN'T DO:
- Single country analysis (use query tools instead)
- More than 10 countries (use rank_countries for larger sets)
- Different metrics per country (all countries get same metrics)

EXAMPLES:
✓ "Compare Brazil vs Indonesia deforestation in 2023"
✓ "Compare Brazil, Peru, Colombia primary forest loss 2020-2024"
✓ "Compare Russia vs Canada vs USA tree cover loss in latest year"
✓ "Compare Amazon countries: Brazil, Peru, Colombia, Ecuador"
✗ "Brazil's deforestation in 2023" (use query_tree_cover_loss)
✗ "Top 20 countries by loss" (use rank_countries)

PARAMETERS:
- countries (required): Array of 2-10 country names
- metric (required): 'loss', 'primary', 'carbon', or 'all'
- year (optional): Specific year or latest
- start_year (optional): For trend comparison
- end_year (optional): For trend comparison
- threshold (optional): Threshold %, defaults to 30""",
            inputSchema={
                "type": "object",
                "properties": {
                    "countries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 10,
                        "description": "List of 2-10 country names to compare"
                    },
                    "metric": {
                        "type": "string",
                        "enum": ["loss", "primary", "carbon", "all"],
                        "description": "Metric to compare"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year to compare (optional, defaults to latest)"
                    },
                    "start_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Start year for trend comparison (optional)"
                    },
                    "end_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "End year for trend comparison (optional)"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [0, 10, 15, 20, 25, 30, 50, 75],
                        "description": "Threshold (optional, defaults to 30)"
                    }
                },
                "required": ["countries", "metric"]
            }
        ),
        
        # ===== TOOL 6: Country Rankings =====
        types.Tool(
            name="rank_countries",
            description="""Rank countries by forest loss, primary forest, or carbon metrics.

WHAT IT DOES:
- Generate top N or bottom N rankings
- Support multiple metrics: loss, primary, carbon emissions, carbon intensity
- Rankings for specific year or cumulative periods
- Optional filtering (e.g., tropical countries only)

WHAT IT DOESN'T DO:
- Provide detailed data for each country (use query tools after ranking)
- Compare specific countries (use compare_countries instead)
- Show trends (use analyze_trend instead)

EXAMPLES:
✓ "Which country had the highest tree cover loss in 2022?"
✓ "Top 10 countries by deforestation in latest year"
✓ "Top 10 countries by primary forest loss in 2023"
✓ "Which countries have highest carbon emissions in 2023?"
✓ "Bottom 10 countries by deforestation in 2023"
✓ "Rank tropical countries by primary share in 2023"
✗ "Brazil's ranking over time" (use analyze_trend)
✗ "Compare top 3 countries" (use compare_countries)

PARAMETERS:
- metric (required): 'loss', 'primary', 'carbon_emissions', 'carbon_intensity', 'primary_share'
- year (optional): Year to rank, defaults to latest
- limit (optional): Number of countries (1-50), defaults to 10
- direction (optional): 'top' or 'bottom', defaults to 'top'
- filter_tropical (optional): Tropical countries only, defaults to false
- threshold (optional): Threshold %, defaults to 30""",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["loss", "primary", "carbon_emissions", "carbon_intensity", "primary_share"],
                        "description": "Metric to rank by"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year (optional, defaults to latest)"
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Number of countries (optional, defaults to 10)"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["top", "bottom"],
                        "description": "Ranking direction (optional, defaults to 'top')"
                    },
                    "filter_tropical": {
                        "type": "boolean",
                        "description": "Tropical countries only (optional, defaults to false)"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [0, 10, 15, 20, 25, 30, 50, 75],
                        "description": "Threshold (optional, defaults to 30)"
                    }
                },
                "required": ["metric"]
            }
        ),
        
        # ===== TOOL 7: Primary Share Calculation =====
        types.Tool(
            name="calculate_primary_share",
            description="""Calculate primary forest as percentage of total tree cover loss.

WHAT IT DOES:
- Calculate (primary forest loss / total tree cover loss) × 100
- Single country/year or trend analysis
- Identify countries with high primary forest impact
- Always uses 30% threshold (standard for primary forest)

WHAT IT DOESN'T DO:
- Provide absolute primary forest values (use query_primary_forest instead)
- Work for non-tropical countries (no primary forest data)
- Calculate for thresholds other than 30%

IMPORTANT: Only meaningful for tropical countries with primary forest.

EXAMPLES:
✓ "What percentage of Brazil's tree cover loss was primary forest in 2023?"
✓ "What is Brazil's primary share in the latest year?"
✓ "Primary share trend for Peru from 2010 to 2024"
✓ "Which countries have primary share above 50% in 2023?"
✗ "Brazil's primary forest loss in 2023" (use query_primary_forest)
✗ "Canada's primary share" (Canada is not tropical)

PARAMETERS:
- country (optional): Country name, omit for all tropical countries
- year (optional): Specific year, omit for trend
- start_year (optional): For trend analysis
- end_year (optional): For trend analysis""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Country name (optional, omit for all tropical countries)"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2002,
                        "maximum": 2024,
                        "description": "Year (optional for single year, omit for trend)"
                    },
                    "start_year": {
                        "type": "integer",
                        "minimum": 2002,
                        "maximum": 2024,
                        "description": "Start year for trend (optional)"
                    },
                    "end_year": {
                        "type": "integer",
                        "minimum": 2002,
                        "maximum": 2024,
                        "description": "End year for trend (optional)"
                    }
                },
                "required": []
            }
        ),
        
        # ===== TOOL 8: Carbon Intensity Calculation =====
        types.Tool(
            name="calculate_carbon_intensity",
            description="""Calculate carbon emissions per hectare of forest loss (CO2e/ha).

WHAT IT DOES:
- Calculate (carbon emissions / tree cover loss) for efficiency metric
- Compare carbon intensity across countries
- Trend analysis of carbon intensity over time
- Identify high-impact deforestation areas

WHAT IT DOESN'T DO:
- Provide absolute carbon values (use query_carbon_data instead)
- Work for countries with zero forest loss (division by zero)
- Support thresholds other than 30%, 50%, 75%

EXAMPLES:
✓ "What is Indonesia's carbon intensity in 2023?"
✓ "Which countries have highest carbon intensity in 2023?"
✓ "How has Brazil's carbon intensity changed since 2010?"
✓ "Carbon intensity trend for Peru 2010-2024"
✗ "Peru's carbon emissions in 2021" (use query_carbon_data)
✗ "Countries with zero emissions" (use query_carbon_data)

PARAMETERS:
- country (optional): Country name, omit for all countries
- year (optional): Specific year, omit for trend
- start_year (optional): For trend analysis
- end_year (optional): For trend analysis
- threshold (optional): 30, 50, or 75, defaults to 30""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Country name (optional)"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year (optional)"
                    },
                    "start_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Start year for trend (optional)"
                    },
                    "end_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "End year for trend (optional)"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [30, 50, 75],
                        "description": "Threshold (optional, defaults to 30)"
                    }
                },
                "required": []
            }
        ),
        
        # ===== TOOL 9: Threshold Comparison =====
        types.Tool(
            name="compare_thresholds",
            description="""Compare forest loss estimates across different canopy density thresholds.

WHAT IT DOES:
- Show how estimates change at 0%, 30%, 50%, 75% thresholds
- Calculate spread/sensitivity to threshold choice
- Single country/year or trend comparison
- Useful for understanding threshold impact on estimates

WHAT IT DOESN'T DO:
- Primary forest (always 30% threshold)
- Carbon for 0%, 10%, 15%, 20%, 25% (data not available)
- Multiple countries (use compare_countries instead)

EXAMPLES:
✓ "For Brazil, how does loss compare at 0%, 30%, and 75% in 2023?"
✓ "Compare Brazil's estimates across thresholds in 2023"
✓ "Which threshold shows highest loss for Russia in 2022?"
✓ "USA's forest loss at different thresholds in latest year"
✗ "Compare Brazil vs Indonesia at 30%" (use compare_countries)

PARAMETERS:
- country (required): Country name
- year (optional): Year, defaults to latest
- thresholds (optional): Array of thresholds, defaults to [0,30,50,75]""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Country name"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year (optional, defaults to latest)"
                    },
                    "thresholds": {
                        "type": "array",
                        "items": {
                            "type": "integer",
                            "enum": [0, 10, 15, 20, 25, 30, 50, 75]
                        },
                        "description": "Thresholds to compare (optional, defaults to [0,30,50,75])"
                    }
                },
                "required": ["country"]
            }
        ),
        
        # ===== TOOL 10: Global Aggregations =====
        types.Tool(
            name="aggregate_global",
            description="""Calculate global aggregations and statistics.

WHAT IT DOES:
- Global totals for any year or year range
- Support metrics: tree cover loss, primary forest, carbon emissions
- Calculate averages, totals, min, max
- Identify peak/lowest years globally

WHAT IT DOESN'T DO:
- Country-specific aggregations (use query tools instead)
- Regional aggregations (need explicit country lists)
- Trend visualization (use analyze_trend instead)

EXAMPLES:
✓ "What was global tree cover loss in 2019?"
✓ "Which year had the highest global deforestation?"
✓ "Which year had the lowest global tree cover loss?"
✓ "Total global primary forest loss in 2023"
✓ "Global carbon emissions from deforestation in 2020"
✓ "What is total global tree cover extent in 2000?"
✗ "Brazil's total loss" (use query_tree_cover_loss or analyze_trend)
✗ "Amazon region total" (need country list, use compare_countries)

PARAMETERS:
- metric (required): 'loss', 'primary', 'carbon_emissions', 'extent_2000', 'extent_2010'
- year (optional): Specific year, omit for all years
- aggregation (optional): 'sum', 'avg', 'min', 'max', defaults to 'sum'
- threshold (optional): Threshold %, defaults to 30""",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["loss", "primary", "carbon_emissions", "extent_2000", "extent_2010"],
                        "description": "Metric to aggregate"
                    },
                    "year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024,
                        "description": "Year (optional, omit for all years)"
                    },
                    "aggregation": {
                        "type": "string",
                        "enum": ["sum", "avg", "min", "max"],
                        "description": "Aggregation function (optional, defaults to 'sum')"
                    },
                    "threshold": {
                        "type": "integer",
                        "enum": [0, 10, 15, 20, 25, 30, 50, 75],
                        "description": "Threshold (optional, defaults to 30)"
                    }
                },
                "required": ["metric"]
            }
        ),
        
        # ===== TOOL 11: List Tropical Countries =====
        types.Tool(
            name="list_tropical_countries",
            description="""List all tropical countries with forest data.

WHAT IT DOES:
- Return complete list of 75 tropical countries
- Show which countries have primary forest data
- Optionally filter by data availability

WHAT IT DOESN'T DO:
- Provide deforestation data (use query tools instead)
- List non-tropical countries (use search_countries instead)

EXAMPLES:
✓ "List all tropical countries in the database"
✓ "Which countries have primary forest data available?"
✓ "Show me all tropical countries"
✗ "List all countries" (use search_countries)
✗ "Show Brazil's data" (use query_tree_cover_loss)

PARAMETERS:
- has_primary_data (optional): Filter to countries with primary forest data""",
            inputSchema={
                "type": "object",
                "properties": {
                    "has_primary_data": {
                        "type": "boolean",
                        "description": "Filter to countries with primary forest data (optional)"
                    }
                },
                "required": []
            }
        ),
        
        # ===== TOOL 12: Search Countries =====
        types.Tool(
            name="search_countries",
            description="""Search for countries in the database with fuzzy matching.

WHAT IT DOES:
- Find countries by partial name
- Handle common misspellings and variations
- Show country aliases (e.g., "USA" → "United States")
- Check if country exists in database

WHAT IT DOESN'T DO:
- Provide forest data (use query tools instead)
- List all countries (use list_tropical_countries for tropical)

EXAMPLES:
✓ "Find countries matching 'cong'"
✓ "Does 'Democratic Republic of Congo' exist in database?"
✓ "Search for countries with 'stan' in name"
✓ "Is Canada in the database?"
✗ "Show Canada's data" (use query_tree_cover_loss)

PARAMETERS:
- search_term (required): Country name or partial name to search""",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Country name or partial name"
                    }
                },
                "required": ["search_term"]
            }
        ),
        
        # ===== TOOL 13: Database Summary =====
        types.Tool(
            name="get_database_summary",
            description="""Get summary statistics about the forest database.

WHAT IT DOES:
- Show row counts for all tables
- Display year coverage
- Show number of countries tracked
- Provide data quality metrics

WHAT IT DOESN'T DO:
- Query specific country data (use query tools instead)
- Show actual forest data (use aggregate_global instead)

EXAMPLES:
✓ "Show database summary"
✓ "How many countries are tracked?"
✓ "What years are covered?"
✓ "Database statistics"

PARAMETERS: None required""",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        # ===== TOOL 14: Subnational tree cover loss =====
        types.Tool(
            name="query_subnational_tree_cover",
            description="""Query state/province (subnational) tree cover loss within a country.

WHAT IT DOES:
- Returns tree cover loss for regions (states/provinces) of a country
- Optionally focus on one region; otherwise lists the top regions for the year

PARAMETERS:
- country (required): Country name
- region (optional): State/province name (subnational1)
- year (optional): 2001-2024 (default: latest)
- threshold (optional): canopy density 0-75 (default: 30)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "Country name"},
                    "region": {"type": "string", "description": "State/province (subnational1)"},
                    "year": {"type": "integer", "description": "Year 2001-2024"},
                    "threshold": {"type": "integer", "description": "Canopy density threshold"}
                },
                "required": ["country"]
            }
        ),

        # ===== TOOL 15: Subnational primary forest loss =====
        types.Tool(
            name="query_subnational_primary_forest",
            description="""Query state/province (subnational) primary forest loss within a country.
Primary forest is fixed at the 30% threshold and covers tropical countries only.

PARAMETERS:
- country (required): Country name
- region (optional): State/province name (subnational1)
- year (optional): 2002-2023 (default: 2023)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "Country name"},
                    "region": {"type": "string", "description": "State/province (subnational1)"},
                    "year": {"type": "integer", "description": "Year 2002-2023"}
                },
                "required": ["country"]
            }
        ),

        # ===== TOOL 16: Subnational carbon =====
        types.Tool(
            name="query_subnational_carbon",
            description="""Query state/province (subnational) forest-carbon emissions within a country.

PARAMETERS:
- country (required): Country name
- region (optional): State/province name (subnational1)
- year (optional): 2001-2024 (default: latest)
- threshold (optional): 30, 50, or 75 (default: 30)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "Country name"},
                    "region": {"type": "string", "description": "State/province (subnational1)"},
                    "year": {"type": "integer", "description": "Year 2001-2024"},
                    "threshold": {"type": "integer", "description": "Carbon threshold 30/50/75"}
                },
                "required": ["country"]
            }
        ),

        # ===== TOOL 17: Rank subnational regions =====
        types.Tool(
            name="rank_subnational_regions",
            description="""Rank state/province (subnational) regions by forest loss or carbon emissions.

WHAT IT DOES:
- Ranks regions by total tree cover loss (default) or carbon emissions
- Within one country (set country) or globally across all regions

PARAMETERS:
- metric (optional): 'tree_cover' (default) or 'carbon'
- country (optional): restrict ranking to one country's regions
- start_year (optional): default 2020
- end_year (optional): default 2024
- threshold (optional): default 30 (carbon uses 30/50/75)
- limit (optional): number of regions (default 10)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "tree_cover or carbon"},
                    "country": {"type": "string", "description": "Optional country filter"},
                    "start_year": {"type": "integer", "description": "Start year"},
                    "end_year": {"type": "integer", "description": "End year"},
                    "threshold": {"type": "integer", "description": "Threshold"},
                    "limit": {"type": "integer", "description": "Number of regions"}
                },
                "required": []
            }
        )
    ]

# ============================================================================
# TOOL HANDLERS
# ============================================================================

@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls with comprehensive error handling"""
    
    try:
        logger.info(f"=== TOOL: {name} ===")
        logger.info(f"Arguments: {json.dumps(arguments, indent=2)}")
        
        # Route to appropriate handler
        if name == "query_tree_cover_loss":
            return await handle_query_tree_cover_loss(arguments)
        elif name == "query_primary_forest":
            return await handle_query_primary_forest(arguments)
        elif name == "query_carbon_data":
            return await handle_query_carbon_data(arguments)
        elif name == "analyze_trend":
            return await handle_analyze_trend(arguments)
        elif name == "compare_countries":
            return await handle_compare_countries(arguments)
        elif name == "rank_countries":
            return await handle_rank_countries(arguments)
        elif name == "calculate_primary_share":
            return await handle_calculate_primary_share(arguments)
        elif name == "calculate_carbon_intensity":
            return await handle_calculate_carbon_intensity(arguments)
        elif name == "compare_thresholds":
            return await handle_compare_thresholds(arguments)
        elif name == "aggregate_global":
            return await handle_aggregate_global(arguments)
        elif name == "list_tropical_countries":
            return await handle_list_tropical_countries(arguments)
        elif name == "search_countries":
            return await handle_search_countries(arguments)
        elif name == "get_database_summary":
            return await handle_get_database_summary(arguments)
        elif name == "query_subnational_tree_cover":
            return await handle_query_subnational_tree_cover(arguments)
        elif name == "query_subnational_primary_forest":
            return await handle_query_subnational_primary_forest(arguments)
        elif name == "query_subnational_carbon":
            return await handle_query_subnational_carbon(arguments)
        elif name == "rank_subnational_regions":
            return await handle_rank_subnational_regions(arguments)
        else:
            return [types.TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]
            
    except Exception as e:
        logger.error(f"Tool execution error: {e}", exc_info=True)
        return [types.TextContent(
            type="text",
            text=f"Error: {str(e)}\n\nPlease check your parameters and try again."
        )]

# ============================================================================
# INDIVIDUAL TOOL HANDLERS
# ============================================================================

async def handle_query_tree_cover_loss(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tree cover loss queries"""
    country = args["country"]
    year = args.get("year", get_latest_year())
    threshold = args.get("threshold", 30)
    include_extent = args.get("include_extent", False)
    
    # Build SQL
    columns = ["country", "year", "threshold", "tree_cover_loss_ha", "loss_rate_pct"]
    if include_extent:
        columns.extend(["extent_2000_ha", "extent_2010_ha", "area_ha"])
    
    sql = f"""
        SELECT {', '.join(columns)}
        FROM fact_tree_cover_loss
        WHERE country = ? AND year = ? AND threshold = ?
    """
    
    results = query_executor.execute_query(sql, (country, year, threshold))
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No data found for {country} in {year} at {threshold}% threshold.\n\n" +
                 f"Suggestions:\n• Check country name spelling\n• Try a different year (2001-2024)\n• Try threshold 30% (FAO standard)"
        )]
    
    row = results[0]
    
    # Format response
    response = f"**Tree Cover Loss: {country} ({year})**\n\n"
    response += f"• Threshold: {threshold}% canopy density\n"
    response += f"• Forest Loss: {format_number(row['tree_cover_loss_ha'])} hectares\n"
    if row.get('loss_rate_pct'):
        response += f"• Loss Rate: {row['loss_rate_pct']:.2f}%\n"
    
    if include_extent:
        if row.get('extent_2000_ha'):
            response += f"• Forest Extent (2000): {format_number(row['extent_2000_ha'])} hectares\n"
        if row.get('extent_2010_ha'):
            response += f"• Forest Extent (2010): {format_number(row['extent_2010_ha'])} hectares\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"Forest loss in {country} during {year}",
        results,
        f"Analyzing {format_number(row['tree_cover_loss_ha'])} hectares of forest loss"
    )
    
    if climate_analysis:
        response += f"\n**Climate Impact:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_query_primary_forest(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle primary forest queries"""
    country = args["country"]
    year = args.get("year", get_latest_year())
    
    sql = """
        SELECT country, year, primary_forest_loss_ha, is_tropical, loss_status
        FROM fact_primary_forest
        WHERE country = ? AND year = ? AND threshold = 30
    """
    
    results = query_executor.execute_query(sql, (country, year))
    
    if not results:
        # Check if country is tropical
        tropical_sql = "SELECT DISTINCT country FROM fact_primary_forest WHERE country = ?"
        tropical_check = query_executor.execute_query(tropical_sql, (country,))
        
        if not tropical_check:
            return [types.TextContent(
                type="text",
                text=f"No primary forest data for {country}.\n\n" +
                     f"{country} may not be a tropical country. Primary forest data is only available for tropical countries.\n\n" +
                     f"Use list_tropical_countries to see which countries have primary forest data."
            )]
        else:
            return [types.TextContent(
                type="text",
                text=f"No data found for {country} in {year}.\n\nPrimary forest data is available from 2002-2024. Try a different year."
            )]
    
    row = results[0]
    
    response = f"**Primary Forest Loss: {country} ({year})**\n\n"
    response += f"• Primary Forest Loss: {format_number(row['primary_forest_loss_ha'])} hectares\n"
    response += f"• Status: {row['loss_status']}\n"
    response += f"• Tropical Country: {'Yes' if row['is_tropical'] else 'No'}\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"Primary (virgin) forest loss in {country} during {year}",
        results,
        "Primary forests are old-growth, undisturbed forests with high biodiversity and carbon storage"
    )
    
    if climate_analysis:
        response += f"\n**Climate Impact:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_query_carbon_data(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle carbon data queries"""
    country = args["country"]
    year = args.get("year", get_latest_year())
    threshold = args.get("threshold", 30)
    
    if threshold not in [30, 50, 75]:
        return [types.TextContent(
            type="text",
            text=f"Carbon data is only available for thresholds 30%, 50%, and 75%.\n\nYou requested {threshold}%. Please use 30, 50, or 75."
        )]
    
    sql = """
        SELECT country, year, threshold,
               carbon_emissions_mg_co2e,
               carbon_emissions_annual_avg,
               carbon_removals_annual_avg,
               carbon_net_flux_annual_avg,
               carbon_density_mg_c_ha,
               carbon_flux_status
        FROM fact_carbon
        WHERE country = ? AND year = ? AND threshold = ?
    """
    
    results = query_executor.execute_query(sql, (country, year, threshold))
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No carbon data found for {country} in {year} at {threshold}% threshold.\n\n" +
                 f"Suggestions:\n• Check country name\n• Try a different year (2001-2024)\n• Try threshold 30, 50, or 75"
        )]
    
    row = results[0]
    net_flux = row.get('carbon_net_flux_annual_avg', 0)
    is_sink = net_flux < 0
    
    response = f"**Carbon Data: {country} ({year})**\n\n"
    response += f"• Threshold: {threshold}%\n"
    response += f"• Carbon Emissions: {format_number(row['carbon_emissions_mg_co2e'])} Mg CO2e\n"
    response += f"• Annual Emissions: {format_number(row['carbon_emissions_annual_avg'])} Mg CO2e/yr\n"
    response += f"• Annual Removals: {format_number(row['carbon_removals_annual_avg'])} Mg CO2/yr\n"
    response += f"• Net Flux: {format_number(net_flux)} Mg CO2e/yr ({'SINK' if is_sink else 'SOURCE'})\n"
    response += f"• Carbon Density: {format_number(row['carbon_density_mg_c_ha'])} Mg C/ha\n"
    response += f"• Status: {row['carbon_flux_status']}\n"
    
    # Get ClimateGPT analysis
    context = "Negative net flux indicates carbon sink (forest absorbing more than emitting). Positive indicates source (emitting more than absorbing)."
    climate_analysis = await call_climategpt(
        f"Carbon emissions from deforestation in {country} during {year}",
        results,
        context
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_analyze_trend(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle trend analysis"""
    metric = args["metric"]
    country = args.get("country")
    start_year = args.get("start_year", 2001)
    end_year = args.get("end_year", get_latest_year())
    threshold = args.get("threshold", 30)
    
    # Build SQL based on metric
    if metric == "loss":
        table = "fact_tree_cover_loss"
        value_col = "tree_cover_loss_ha"
        metric_name = "Tree Cover Loss"
    elif metric == "primary":
        table = "fact_primary_forest"
        value_col = "primary_forest_loss_ha"
        metric_name = "Primary Forest Loss"
        threshold = 30  # Always 30 for primary
    elif metric == "carbon_emissions":
        table = "fact_carbon"
        value_col = "carbon_emissions_mg_co2e"
        metric_name = "Carbon Emissions"
    elif metric == "carbon_removals":
        table = "fact_carbon"
        value_col = "carbon_removals_annual_avg"
        metric_name = "Carbon Removals"
    else:
        return [types.TextContent(
            type="text",
            text=f"Unknown metric: {metric}. Use 'loss', 'primary', 'carbon_emissions', or 'carbon_removals'."
        )]
    
    if country:
        sql = f"""
            SELECT year, SUM({value_col}) as total_value
            FROM {table}
            WHERE country = ? AND year BETWEEN ? AND ? AND threshold = ?
            GROUP BY year
            ORDER BY year
        """
        params = (country, start_year, end_year, threshold)
    else:
        sql = f"""
            SELECT year, SUM({value_col}) as total_value
            FROM {table}
            WHERE year BETWEEN ? AND ? AND threshold = ?
            GROUP BY year
            ORDER BY year
        """
        params = (start_year, end_year, threshold)
    
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No trend data found for {country or 'global'} from {start_year}-{end_year}."
        )]
    
    # Format response
    title = f"**{metric_name} Trend: {country or 'Global'} ({start_year}-{end_year})**\n\n"
    
    lines = []
    for row in results:
        year_val = row['year']
        value = row['total_value']
        lines.append(f"• {year_val}: {format_number(value)} {'hectares' if 'loss' in metric.lower() else 'Mg CO2e'}")
    
    # Calculate change
    if len(results) >= 2:
        first_value = results[0]['total_value']
        last_value = results[-1]['total_value']
        change_pct = ((last_value - first_value) / first_value * 100) if first_value > 0 else 0
        change_abs = last_value - first_value
        
        summary = f"\n**Overall Change ({results[0]['year']}-{results[-1]['year']}):**\n"
        summary += f"• Absolute: {format_number(change_abs)} ({'increase' if change_abs > 0 else 'decrease'})\n"
        summary += f"• Percentage: {change_pct:+.1f}%\n"
    else:
        summary = ""
    
    response = title + "\n".join(lines) + summary
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"{metric_name} trend for {country or 'global'} from {start_year} to {end_year}",
        results,
        "Analyze temporal patterns and climate implications"
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_compare_countries(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle country comparisons"""
    countries = args["countries"]
    metric = args["metric"]
    year = args.get("year", get_latest_year())
    threshold = args.get("threshold", 30)
    
    if len(countries) < 2:
        return [types.TextContent(
            type="text",
            text="Please provide at least 2 countries to compare."
        )]
    
    if len(countries) > 10:
        return [types.TextContent(
            type="text",
            text="Maximum 10 countries can be compared. Use rank_countries for larger sets."
        )]
    
    # Build SQL based on metric
    if metric == "loss":
        sql = """
            SELECT country, year, tree_cover_loss_ha as value
            FROM fact_tree_cover_loss
            WHERE country IN ({}) AND year = ? AND threshold = ?
            ORDER BY tree_cover_loss_ha DESC
        """.format(','.join('?' * len(countries)))
        params = tuple(countries) + (year, threshold)
    elif metric == "primary":
        sql = """
            SELECT country, year, primary_forest_loss_ha as value
            FROM fact_primary_forest
            WHERE country IN ({}) AND year = ? AND threshold = 30
            ORDER BY primary_forest_loss_ha DESC
        """.format(','.join('?' * len(countries)))
        params = tuple(countries) + (year,)
    elif metric == "carbon":
        sql = """
            SELECT country, year, carbon_emissions_mg_co2e as value
            FROM fact_carbon
            WHERE country IN ({}) AND year = ? AND threshold = ?
            ORDER BY carbon_emissions_mg_co2e DESC
        """.format(','.join('?' * len(countries)))
        params = tuple(countries) + (year, threshold)
    else:
        return [types.TextContent(
            type="text",
            text=f"Unknown metric: {metric}. Use 'loss', 'primary', or 'carbon'."
        )]
    
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No data found for comparison in {year}. Check country names and try again."
        )]
    
    # Format response
    metric_name = {
        "loss": "Tree Cover Loss",
        "primary": "Primary Forest Loss",
        "carbon": "Carbon Emissions"
    }.get(metric, metric)
    
    response = f"**{metric_name} Comparison ({year})**\n\n"
    
    for i, row in enumerate(results, 1):
        response += f"{i}. **{row['country']}**: {format_number(row['value'])} "
        response += "hectares\n" if metric in ["loss", "primary"] else "Mg CO2e\n"
    
    # Add relative comparison
    if len(results) >= 2:
        highest = results[0]
        lowest = results[-1]
        ratio = highest['value'] / lowest['value'] if lowest['value'] > 0 else 0
        response += f"\n**Key Finding:** {highest['country']}'s {metric_name.lower()} is {ratio:.1f}x higher than {lowest['country']}'s\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"Comparison of {metric_name.lower()} across {len(results)} countries in {year}",
        results,
        "Compare and analyze differences between countries"
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_rank_countries(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle country rankings"""
    metric = args["metric"]
    year = args.get("year", get_latest_year())
    limit = args.get("limit", 10)
    direction = args.get("direction", "top")
    filter_tropical = args.get("filter_tropical", False)
    threshold = args.get("threshold", 30)
    
    order = "DESC" if direction == "top" else "ASC"
    
    # Build SQL based on metric
    if metric == "loss":
        sql = f"""
            SELECT country, tree_cover_loss_ha as value
            FROM fact_tree_cover_loss
            WHERE year = ? AND threshold = ?
            {"AND country IN (SELECT DISTINCT country FROM fact_primary_forest WHERE is_tropical = 1)" if filter_tropical else ""}
            ORDER BY tree_cover_loss_ha {order}
            LIMIT ?
        """
        params = (year, threshold, limit)
        unit = "hectares"
    elif metric == "primary":
        sql = f"""
            SELECT country, primary_forest_loss_ha as value
            FROM fact_primary_forest
            WHERE year = ? AND threshold = 30
            ORDER BY primary_forest_loss_ha {order}
            LIMIT ?
        """
        params = (year, limit)
        unit = "hectares"
    elif metric == "carbon_emissions":
        sql = f"""
            SELECT country, carbon_emissions_mg_co2e as value
            FROM fact_carbon
            WHERE year = ? AND threshold = ?
            {"AND country IN (SELECT DISTINCT country FROM fact_primary_forest WHERE is_tropical = 1)" if filter_tropical else ""}
            ORDER BY carbon_emissions_mg_co2e {order}
            LIMIT ?
        """
        params = (year, threshold, limit)
        unit = "Mg CO2e"
    elif metric == "carbon_intensity":
        sql = f"""
            SELECT t.country,
                   (c.carbon_emissions_mg_co2e * 1.0 / NULLIF(t.tree_cover_loss_ha, 0)) as value
            FROM fact_carbon c
            JOIN fact_tree_cover_loss t
              ON t.country = c.country AND t.year = c.year AND t.threshold = c.threshold
            WHERE c.year = ? AND c.threshold = ?
            {"AND c.country IN (SELECT DISTINCT country FROM fact_primary_forest WHERE is_tropical = 1)" if filter_tropical else ""}
            ORDER BY value {order}
            LIMIT ?
        """
        params = (year, threshold, limit)
        unit = "Mg CO2e/ha"
    elif metric == "primary_share":
        sql = f"""
            SELECT t.country,
                   ROUND(100.0 * p.primary_forest_loss_ha / NULLIF(t.tree_cover_loss_ha, 0), 2) as value
            FROM fact_tree_cover_loss t
            JOIN fact_primary_forest p
              ON p.country = t.country AND p.year = t.year
            WHERE t.year = ? AND t.threshold = 30
            ORDER BY value {order}
            LIMIT ?
        """
        params = (year, limit)
        unit = "%"
    else:
        return [types.TextContent(
            type="text",
            text=f"Unknown metric: {metric}. Use 'loss', 'primary', 'carbon_emissions', 'carbon_intensity', or 'primary_share'."
        )]
    
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No ranking data found for {year}."
        )]
    
    # Format response
    metric_name = metric.replace("_", " ").title()
    response = f"**{direction.title()} {limit} Countries by {metric_name} ({year})**\n\n"
    
    if filter_tropical:
        response += "*Filtered to tropical countries only*\n\n"
    
    for i, row in enumerate(results, 1):
        response += f"{i}. **{row['country']}**: {format_number(row['value'])} {unit}\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"{direction.title()} {limit} countries by {metric_name.lower()} in {year}",
        results,
        "Analyze patterns and climate implications of these rankings"
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_calculate_primary_share(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle primary share calculations"""
    country = args.get("country")
    year = args.get("year")
    start_year = args.get("start_year")
    end_year = args.get("end_year")
    
    # Determine if single year or trend
    if year:
        # Single year
        if country:
            sql = """
                SELECT t.country, t.year,
                       t.tree_cover_loss_ha,
                       p.primary_forest_loss_ha,
                       ROUND(100.0 * p.primary_forest_loss_ha / NULLIF(t.tree_cover_loss_ha, 0), 2) as primary_share_pct
                FROM fact_tree_cover_loss t
                JOIN fact_primary_forest p
                  ON p.country = t.country AND p.year = t.year
                WHERE t.country = ? AND t.year = ? AND t.threshold = 30
            """
            params = (country, year)
        else:
            sql = """
                SELECT t.country, t.year,
                       t.tree_cover_loss_ha,
                       p.primary_forest_loss_ha,
                       ROUND(100.0 * p.primary_forest_loss_ha / NULLIF(t.tree_cover_loss_ha, 0), 2) as primary_share_pct
                FROM fact_tree_cover_loss t
                JOIN fact_primary_forest p
                  ON p.country = t.country AND p.year = t.year
                WHERE t.year = ? AND t.threshold = 30
                ORDER BY primary_share_pct DESC
                LIMIT 20
            """
            params = (year,)
    else:
        # Trend
        if not start_year:
            start_year = 2002
        if not end_year:
            end_year = get_latest_year()
        
        if country:
            sql = """
                SELECT t.country, t.year,
                       t.tree_cover_loss_ha,
                       p.primary_forest_loss_ha,
                       ROUND(100.0 * p.primary_forest_loss_ha / NULLIF(t.tree_cover_loss_ha, 0), 2) as primary_share_pct
                FROM fact_tree_cover_loss t
                JOIN fact_primary_forest p
                  ON p.country = t.country AND p.year = t.year
                WHERE t.country = ? AND t.year BETWEEN ? AND ? AND t.threshold = 30
                ORDER BY t.year
            """
            params = (country, start_year, end_year)
        else:
            return [types.TextContent(
                type="text",
                text="For trend analysis without specifying a country, please specify start_year and end_year, or use rank_countries with metric='primary_share' for a single year."
            )]
    
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No primary share data found. Note: Primary forest data only exists for tropical countries from 2002-2024."
        )]
    
    # Format response
    if year and country:
        # Single country/year
        row = results[0]
        response = f"**Primary Forest Share: {country} ({year})**\n\n"
        response += f"• Total Tree Cover Loss: {format_number(row['tree_cover_loss_ha'])} hectares\n"
        response += f"• Primary Forest Loss: {format_number(row['primary_forest_loss_ha'])} hectares\n"
        response += f"• Primary Share: {row['primary_share_pct']}%\n"
    elif year:
        # All countries, single year
        response = f"**Countries by Primary Forest Share ({year})**\n\n"
        for i, row in enumerate(results, 1):
            response += f"{i}. **{row['country']}**: {row['primary_share_pct']}% "
            response += f"({format_number(row['primary_forest_loss_ha'])} of {format_number(row['tree_cover_loss_ha'])} ha)\n"
    else:
        # Trend for single country
        response = f"**Primary Forest Share Trend: {country} ({start_year}-{end_year})**\n\n"
        for row in results:
            response += f"• {row['year']}: {row['primary_share_pct']}% "
            response += f"({format_number(row['primary_forest_loss_ha'])} of {format_number(row['tree_cover_loss_ha'])} ha)\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"Primary forest as percentage of total forest loss",
        results,
        "Primary forests have highest biodiversity and carbon storage. High primary share indicates more critical loss."
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_calculate_carbon_intensity(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle carbon intensity calculations"""
    country = args.get("country")
    year = args.get("year")
    start_year = args.get("start_year")
    end_year = args.get("end_year")
    threshold = args.get("threshold", 30)
    
    if threshold not in [30, 50, 75]:
        return [types.TextContent(
            type="text",
            text="Carbon data only available for thresholds 30, 50, and 75."
        )]
    
    # Determine if single year or trend
    if year:
        if country:
            sql = """
                SELECT t.country, t.year,
                       t.tree_cover_loss_ha,
                       c.carbon_emissions_mg_co2e,
                       ROUND(c.carbon_emissions_mg_co2e * 1.0 / NULLIF(t.tree_cover_loss_ha, 0), 2) as co2e_per_ha
                FROM fact_carbon c
                JOIN fact_tree_cover_loss t
                  ON t.country = c.country AND t.year = c.year AND t.threshold = c.threshold
                WHERE c.country = ? AND c.year = ? AND c.threshold = ?
            """
            params = (country, year, threshold)
        else:
            sql = """
                SELECT t.country, t.year,
                       t.tree_cover_loss_ha,
                       c.carbon_emissions_mg_co2e,
                       ROUND(c.carbon_emissions_mg_co2e * 1.0 / NULLIF(t.tree_cover_loss_ha, 0), 2) as co2e_per_ha
                FROM fact_carbon c
                JOIN fact_tree_cover_loss t
                  ON t.country = c.country AND t.year = c.year AND t.threshold = c.threshold
                WHERE c.year = ? AND c.threshold = ?
                ORDER BY co2e_per_ha DESC
                LIMIT 20
            """
            params = (year, threshold)
    else:
        # Trend
        if not start_year:
            start_year = 2001
        if not end_year:
            end_year = get_latest_year()
        
        if country:
            sql = """
                SELECT t.country, t.year,
                       t.tree_cover_loss_ha,
                       c.carbon_emissions_mg_co2e,
                       ROUND(c.carbon_emissions_mg_co2e * 1.0 / NULLIF(t.tree_cover_loss_ha, 0), 2) as co2e_per_ha
                FROM fact_carbon c
                JOIN fact_tree_cover_loss t
                  ON t.country = c.country AND t.year = c.year AND t.threshold = c.threshold
                WHERE c.country = ? AND c.year BETWEEN ? AND ? AND c.threshold = ?
                ORDER BY t.year
            """
            params = (country, start_year, end_year, threshold)
        else:
            return [types.TextContent(
                type="text",
                text="For trend analysis without country, specify start_year and end_year, or use rank_countries with metric='carbon_intensity'."
            )]
    
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text="No carbon intensity data found."
        )]
    
    # Format response
    if year and country:
        row = results[0]
        response = f"**Carbon Intensity: {country} ({year})**\n\n"
        response += f"• Tree Cover Loss: {format_number(row['tree_cover_loss_ha'])} hectares\n"
        response += f"• Carbon Emissions: {format_number(row['carbon_emissions_mg_co2e'])} Mg CO2e\n"
        response += f"• Carbon Intensity: {row['co2e_per_ha']} Mg CO2e per hectare\n"
    elif year:
        response = f"**Countries by Carbon Intensity ({year})**\n\n"
        for i, row in enumerate(results, 1):
            response += f"{i}. **{row['country']}**: {row['co2e_per_ha']} Mg CO2e/ha\n"
    else:
        response = f"**Carbon Intensity Trend: {country} ({start_year}-{end_year})**\n\n"
        for row in results:
            response += f"• {row['year']}: {row['co2e_per_ha']} Mg CO2e/ha\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        "Carbon emissions per hectare of forest loss",
        results,
        "Carbon intensity shows how much CO2 is released per hectare lost. Higher intensity indicates carbon-rich forests."
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_compare_thresholds(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle threshold comparisons"""
    country = args["country"]
    year = args.get("year", get_latest_year())
    thresholds = args.get("thresholds", [0, 30, 50, 75])
    
    placeholders = ','.join('?' * len(thresholds))
    sql = f"""
        SELECT country, year, threshold, tree_cover_loss_ha
        FROM fact_tree_cover_loss
        WHERE country = ? AND year = ? AND threshold IN ({placeholders})
        ORDER BY threshold
    """
    
    params = (country, year) + tuple(thresholds)
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text=f"No data found for {country} in {year}."
        )]
    
    response = f"**Threshold Comparison: {country} ({year})**\n\n"
    
    for row in results:
        thr = row['threshold']
        val = row['tree_cover_loss_ha']
        response += f"• {thr}% threshold: {format_number(val)} hectares\n"
    
    # Calculate spread
    if len(results) >= 2:
        min_val = min(r['tree_cover_loss_ha'] for r in results)
        max_val = max(r['tree_cover_loss_ha'] for r in results)
        spread = max_val - min_val
        response += f"\n**Spread:** {format_number(spread)} hectares ({((spread/max_val)*100):.1f}% variation)\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"Impact of canopy density threshold on forest loss estimates for {country}",
        results,
        "Lower thresholds include sparser forests, higher thresholds focus on denser forests."
    )
    
    if climate_analysis:
        response += f"\n**Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_aggregate_global(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle global aggregations"""
    metric = args["metric"]
    year = args.get("year")
    aggregation = args.get("aggregation", "sum")
    threshold = args.get("threshold", 30)
    
    # Build SQL based on metric
    if metric == "loss":
        table = "fact_tree_cover_loss"
        value_col = "tree_cover_loss_ha"
        unit = "hectares"
    elif metric == "primary":
        table = "fact_primary_forest"
        value_col = "primary_forest_loss_ha"
        unit = "hectares"
        threshold = 30
    elif metric == "carbon_emissions":
        table = "fact_carbon"
        value_col = "carbon_emissions_mg_co2e"
        unit = "Mg CO2e"
    elif metric == "extent_2000":
        table = "fact_tree_cover_loss"
        value_col = "extent_2000_ha"
        unit = "hectares"
        aggregation = "sum"
    elif metric == "extent_2010":
        table = "fact_tree_cover_loss"
        value_col = "extent_2010_ha"
        unit = "hectares"
        aggregation = "sum"
    else:
        return [types.TextContent(
            type="text",
            text=f"Unknown metric: {metric}"
        )]
    
    agg_func = aggregation.upper()
    
    if year:
        sql = f"""
            SELECT {agg_func}({value_col}) as result,
                   COUNT(DISTINCT country) as countries
            FROM {table}
            WHERE year = ? AND threshold = ?
        """
        params = (year, threshold)
    else:
        sql = f"""
            SELECT year,
                   {agg_func}({value_col}) as result,
                   COUNT(DISTINCT country) as countries
            FROM {table}
            WHERE threshold = ?
            GROUP BY year
            ORDER BY result DESC
            LIMIT 10
        """
        params = (threshold,)
    
    results = query_executor.execute_query(sql, params)
    
    if not results:
        return [types.TextContent(
            type="text",
            text="No data found for aggregation."
        )]
    
    metric_name = metric.replace("_", " ").title()
    
    if year:
        row = results[0]
        response = f"**Global {metric_name} ({year})**\n\n"
        response += f"• {aggregation.title()}: {format_number(row['result'])} {unit}\n"
        response += f"• Countries: {row['countries']}\n"
        response += f"• Threshold: {threshold}%\n"
    else:
        response = f"**Global {metric_name} - {aggregation.title()} by Year**\n\n"
        response += f"Top 10 years:\n\n"
        for i, row in enumerate(results, 1):
            response += f"{i}. **{row['year']}**: {format_number(row['result'])} {unit}\n"
    
    # Get ClimateGPT analysis
    climate_analysis = await call_climategpt(
        f"Global {metric_name.lower()} statistics",
        results,
        "Analyze global patterns and climate implications"
    )
    
    if climate_analysis:
        response += f"\n**Climate Analysis:**\n{climate_analysis}"
    
    return [types.TextContent(type="text", text=response)]

async def handle_list_tropical_countries(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tropical country listing"""
    has_primary_data = args.get("has_primary_data", False)
    
    if has_primary_data:
        sql = """
            SELECT DISTINCT country
            FROM fact_primary_forest
            WHERE is_tropical = 1
            ORDER BY country
        """
    else:
        # Get from semantic metadata
        tropical_countries = metadata_manager.semantic.tropical_countries
        countries = sorted(list(tropical_countries))
        
        response = f"**Tropical Countries ({len(countries)} total)**\n\n"
        for i, country in enumerate(countries, 1):
            response += f"{i}. {country}\n"
        
        return [types.TextContent(type="text", text=response)]
    
    results = query_executor.execute_query(sql)
    
    response = f"**Tropical Countries with Primary Forest Data ({len(results)} total)**\n\n"
    for i, row in enumerate(results, 1):
        response += f"{i}. {row['country']}\n"
    
    return [types.TextContent(type="text", text=response)]

async def handle_search_countries(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle country search"""
    search_term = args["search_term"].lower()
    
    sql = """
        SELECT DISTINCT country
        FROM fact_tree_cover_loss
        WHERE LOWER(country) LIKE ?
        ORDER BY country
        LIMIT 20
    """
    
    results = query_executor.execute_query(sql, (f"%{search_term}%",))
    
    if not results:
        # Try fuzzy matching with aliases
        alias_matches = []
        for alias, canonical in query_router.country_mappings.items():
            if search_term in alias.lower():
                alias_matches.append(canonical)
        
        if alias_matches:
            response = f"**No exact matches, but found aliases:**\n\n"
            for i, country in enumerate(set(alias_matches), 1):
                response += f"{i}. {country}\n"
        else:
            response = f"No countries found matching '{search_term}'.\n\n"
            response += "Try:\n• Checking spelling\n• Using partial name\n• Using search_countries with broader term"
        
        return [types.TextContent(type="text", text=response)]
    
    response = f"**Countries matching '{search_term}' ({len(results)} found)**\n\n"
    for i, row in enumerate(results, 1):
        response += f"{i}. {row['country']}\n"
    
    return [types.TextContent(type="text", text=response)]

async def handle_get_database_summary(args: Dict[str, Any]) -> List[types.TextContent]:
    """Handle database summary"""
    
    # Get row counts
    tables = {
        "fact_tree_cover_loss": "Tree Cover Loss",
        "fact_primary_forest": "Primary Forest",
        "fact_carbon": "Carbon Data"
    }
    
    response = "**Forest Database Summary**\n\n"
    
    for table, name in tables.items():
        sql = f"SELECT COUNT(*) as count FROM {table}"
        result = query_executor.execute_query(sql)
        if result:
            response += f"• {name}: {format_number(result[0]['count'])} records\n"
    
    # Get year coverage
    sql = "SELECT MIN(year) as min_year, MAX(year) as max_year FROM fact_tree_cover_loss"
    result = query_executor.execute_query(sql)
    if result:
        response += f"\n**Year Coverage:** {result[0]['min_year']}-{result[0]['max_year']}\n"
    
    # Get country count
    sql = "SELECT COUNT(DISTINCT country) as count FROM fact_tree_cover_loss"
    result = query_executor.execute_query(sql)
    if result:
        response += f"**Countries Tracked:** {result[0]['count']}\n"
    
    # Get tropical country count
    sql = "SELECT COUNT(DISTINCT country) as count FROM fact_primary_forest WHERE is_tropical = 1"
    result = query_executor.execute_query(sql)
    if result:
        response += f"**Tropical Countries:** {result[0]['count']}\n"
    
    # Get threshold info
    sql = "SELECT DISTINCT threshold FROM fact_tree_cover_loss ORDER BY threshold"
    result = query_executor.execute_query(sql)
    if result:
        thresholds = [str(r['threshold']) for r in result]
        response += f"**Thresholds Available:** {', '.join(thresholds)}%\n"
    
    response += "\n**Data Source:** Hansen et al. 2024, Global Forest Watch"
    
    return [types.TextContent(type="text", text=response)]

# ============================================================================
# SUBNATIONAL TOOL HANDLERS
# ============================================================================

async def handle_query_subnational_tree_cover(args):
    """Subnational tree cover loss for a country's regions (or one region)."""
    country = args["country"]
    region = args.get("region")
    year = args.get("year", get_latest_year())
    threshold = args.get("threshold", 30)

    if region:
        sql = """SELECT country, subnational1, year, threshold, tree_cover_loss_ha, loss_rate_pct
                 FROM fact_subnational_tree_cover_loss
                 WHERE country = ? AND subnational1 = ? AND year = ? AND threshold = ?"""
        results = query_executor.execute_query(sql, (country, region, year, threshold))
    else:
        sql = """SELECT subnational1, tree_cover_loss_ha
                 FROM fact_subnational_tree_cover_loss
                 WHERE country = ? AND year = ? AND threshold = ?
                 ORDER BY tree_cover_loss_ha DESC LIMIT 15"""
        results = query_executor.execute_query(sql, (country, year, threshold))

    if not results:
        return [types.TextContent(type="text",
            text=f"No subnational tree cover data for {country}"
                 + (f" / {region}" if region else "") + f" in {year} at {threshold}%.")]

    if region:
        row = results[0]
        response = f"**Subnational Tree Cover Loss: {region}, {country} ({year})**\n\n"
        response += f"- Threshold: {threshold}% canopy density\n"
        response += f"- Forest Loss: {format_number(row['tree_cover_loss_ha'])} hectares\n"
        if row.get('loss_rate_pct'):
            response += f"- Loss Rate: {row['loss_rate_pct']:.2f}%\n"
    else:
        response = f"**Top regions by tree cover loss: {country} ({year}, {threshold}%)**\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. {r['subnational1']}: {format_number(r['tree_cover_loss_ha'])} ha\n"

    analysis = await call_climategpt(
        f"Subnational forest loss in {country} during {year}", results)
    if analysis:
        response += f"\n**Climate Impact:**\n{analysis}"
    return [types.TextContent(type="text", text=response)]


async def handle_query_subnational_primary_forest(args):
    """Subnational primary forest loss (threshold fixed at 30)."""
    country = args["country"]
    region = args.get("region")
    year = args.get("year", 2023)

    if region:
        sql = """SELECT country, subnational1, year, primary_forest_loss_ha, is_tropical, loss_status
                 FROM fact_subnational_primary_forest
                 WHERE country = ? AND subnational1 = ? AND year = ?"""
        results = query_executor.execute_query(sql, (country, region, year))
    else:
        sql = """SELECT subnational1, primary_forest_loss_ha
                 FROM fact_subnational_primary_forest
                 WHERE country = ? AND year = ?
                 ORDER BY primary_forest_loss_ha DESC LIMIT 15"""
        results = query_executor.execute_query(sql, (country, year))

    if not results:
        return [types.TextContent(type="text",
            text=f"No subnational primary forest data for {country}"
                 + (f" / {region}" if region else "") + f" in {year}. "
                 "Primary forest covers tropical countries only (2002-2023).")]

    if region:
        row = results[0]
        response = f"**Subnational Primary Forest Loss: {region}, {country} ({year})**\n\n"
        response += f"- Threshold: 30% (fixed for primary forest)\n"
        response += f"- Primary Forest Loss: {format_number(row['primary_forest_loss_ha'])} hectares\n"
        response += f"- Status: {row.get('loss_status')}\n"
    else:
        response = f"**Top regions by primary forest loss: {country} ({year})**\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. {r['subnational1']}: {format_number(r['primary_forest_loss_ha'])} ha\n"

    analysis = await call_climategpt(
        f"Subnational primary forest loss in {country} during {year}", results)
    if analysis:
        response += f"\n**Climate Impact:**\n{analysis}"
    return [types.TextContent(type="text", text=response)]


async def handle_query_subnational_carbon(args):
    """Subnational forest-carbon emissions."""
    country = args["country"]
    region = args.get("region")
    year = args.get("year", get_latest_year())
    threshold = args.get("threshold", 30)
    if threshold not in (30, 50, 75):
        threshold = 30

    if region:
        sql = """SELECT country, subnational1, year, threshold, carbon_emissions_mg_co2e,
                        carbon_net_flux_annual_avg, carbon_flux_status
                 FROM fact_subnational_carbon
                 WHERE country = ? AND subnational1 = ? AND year = ? AND threshold = ?"""
        results = query_executor.execute_query(sql, (country, region, year, threshold))
    else:
        sql = """SELECT subnational1, carbon_emissions_mg_co2e
                 FROM fact_subnational_carbon
                 WHERE country = ? AND year = ? AND threshold = ?
                 ORDER BY carbon_emissions_mg_co2e DESC LIMIT 15"""
        results = query_executor.execute_query(sql, (country, year, threshold))

    if not results:
        return [types.TextContent(type="text",
            text=f"No subnational carbon data for {country}"
                 + (f" / {region}" if region else "") + f" in {year} at {threshold}%.")]

    if region:
        row = results[0]
        response = f"**Subnational Forest Carbon: {region}, {country} ({year})**\n\n"
        response += f"- Threshold: {threshold}%\n"
        response += f"- Gross Emissions: {format_number(row['carbon_emissions_mg_co2e'])} Mg CO2e\n"
        if row.get('carbon_flux_status'):
            response += f"- Flux Status: {row['carbon_flux_status']}\n"
    else:
        response = f"**Top regions by carbon emissions: {country} ({year}, {threshold}%)**\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. {r['subnational1']}: {format_number(r['carbon_emissions_mg_co2e'])} Mg CO2e\n"

    analysis = await call_climategpt(
        f"Subnational forest carbon emissions in {country} during {year}", results)
    if analysis:
        response += f"\n**Climate Impact:**\n{analysis}"
    return [types.TextContent(type="text", text=response)]


async def handle_rank_subnational_regions(args):
    """Rank subnational regions by tree cover loss or carbon emissions."""
    metric = args.get("metric", "tree_cover")
    country = args.get("country")
    start_year = args.get("start_year", 2020)
    end_year = args.get("end_year", 2024)
    threshold = args.get("threshold", 30)
    limit = args.get("limit", 10)

    if metric == "carbon":
        if threshold not in (30, 50, 75):
            threshold = 30
        table, value = "fact_subnational_carbon", "carbon_emissions_mg_co2e"
        unit = "Mg CO2e"
    else:
        table, value = "fact_subnational_tree_cover_loss", "tree_cover_loss_ha"
        unit = "ha"

    params = [threshold, start_year, end_year]
    where_country = ""
    if country:
        where_country = " AND country = ?"
        params.append(country)

    sql = f"""SELECT country, subnational1, SUM({value}) AS total
              FROM {table}
              WHERE threshold = ? AND year BETWEEN ? AND ?{where_country}
              GROUP BY country, subnational1
              ORDER BY total DESC LIMIT ?"""
    params.append(limit)
    results = query_executor.execute_query(sql, tuple(params))

    if not results:
        return [types.TextContent(type="text", text="No subnational data found for that query.")]

    scope = f"in {country}" if country else "globally"
    response = f"**Top {limit} regions by {metric.replace('_',' ')} {scope} ({start_year}-{end_year}, {threshold}%)**\n\n"
    for i, r in enumerate(results, 1):
        label = f"{r['subnational1']}, {r['country']}" if not country else r['subnational1']
        response += f"{i}. {label}: {format_number(r['total'])} {unit}\n"

    analysis = await call_climategpt(
        f"Top {metric} regions {scope} {start_year}-{end_year}", results)
    if analysis:
        response += f"\n**Climate Impact:**\n{analysis}"
    return [types.TextContent(type="text", text=response)]


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Run the MCP server"""
    logger.info("Starting Forest Data MCP Server (LLM narrative via llm_client)...")

    # Verify database
    if not Path(DATABASE_PATH).exists():
        logger.error(f"Database not found: {DATABASE_PATH}")
        sys.exit(1)

    logger.info(f"Using database: {DATABASE_PATH}")
    logger.info(f"LLM provider: {llm_client.provider_info()}")
    
    # Run server
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="forest-data-analyzer",
                server_version="2.0.0",
                capabilities={}
            )
        )

if __name__ == "__main__":
    asyncio.run(main())
