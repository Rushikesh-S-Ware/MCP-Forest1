"""
MCP Server for Claude Desktop with ClimateGPT Integration
Provides forest data query tools enhanced with climate analysis
"""
import asyncio
import json
import sys
import logging
import os
import re
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional
import sqlite3

import httpx
from mcp.server import Server
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

# Import your existing components
from nexus.mcp.router import QueryRouter
from nexus.mcp.sql_generator import SafeSQLGenerator
from nexus.config.settings import settings
from nexus.data.metadata.metadata_manager import metadata_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ClimateGPT Configuration from environment
CLIMATEGPT_URL = os.getenv("CLIMATEGPT_URL", "https://erasmus.ai/models/climategpt_8b_test/v1/chat/completions")
CLIMATEGPT_USER = os.getenv("CLIMATEGPT_USER", "ai")
CLIMATEGPT_PASSWORD = os.getenv("CLIMATEGPT_PASSWORD", "4climate")
CLIMATEGPT_MODEL = os.getenv("CLIMATEGPT_MODEL", "/cache/climategpt_8b_test")

# Database path
DATABASE_PATH = os.getenv("DATABASE_PATH", str(settings.sqlite_db_path))

# Simple QueryExecutor since the import fails
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

# Create MCP server instance
app = Server("forest-climate-analyzer")

async def call_climategpt(question: str, data: List[Dict], metadata: Dict = None) -> str:
    """
    Call ClimateGPT API for enhanced climate analysis
    """
    logger.info(f"=== CALLING CLIMATEGPT ===")
    logger.info(f"Question: {question}")
    logger.info(f"Data rows: {len(data)}")
    
    if not data:
        return ""
    
    # Format data for ClimateGPT
    data_text = format_data_for_climategpt(data, metadata)
    
    # Prepare the prompt based on query type
    intent = metadata.get("intent", "simple_metric") if metadata else "simple_metric"
    
    system_prompt = """You are a climate science expert analyzing Global Forest Watch deforestation data.
    Provide insightful analysis including:
    - Key trends and patterns with specific percentages
    - Climate change implications
    - Environmental and ecosystem impacts
    - Policy recommendations if relevant
    Format numbers with commas (e.g., 2,805,356 hectares)."""
    
    user_prompt = f"""Question: {question}

Forest Data:
{data_text}

Please provide comprehensive climate analysis with specific insights about:
1. What this data reveals about deforestation patterns and climate impact
2. Key trends, inflection points, or concerning changes
3. Environmental implications for biodiversity and carbon emissions
4. Context about what's driving these changes (if apparent from the data)
5. How this compares to global climate goals"""

    try:
        # Create auth header
        auth = base64.b64encode(f"{CLIMATEGPT_USER}:{CLIMATEGPT_PASSWORD}".encode()).decode()
        
        logger.info(f"Calling ClimateGPT API at {CLIMATEGPT_URL}")
        
        # Call ClimateGPT API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                CLIMATEGPT_URL,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": CLIMATEGPT_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "max_tokens": 800,
                    "temperature": 0.7
                }
            )
        
        logger.info(f"ClimateGPT response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            analysis = result["choices"][0]["message"]["content"]
            logger.info("=== CLIMATEGPT SUCCESS ===")
            return analysis
        else:
            logger.error(f"ClimateGPT error: {response.status_code}")
            logger.error(f"Response: {response.text}")
            return ""
            
    except Exception as e:
        logger.error(f"ClimateGPT API call failed: {e}")
        return ""

def format_data_for_climategpt(data: List[Dict], metadata: Dict = None) -> str:
    """Format query results for ClimateGPT analysis"""
    if not data:
        return "No data available"
    
    intent = metadata.get("intent", "") if metadata else ""
    
    # For trend queries - format as clear time series
    if "trend" in intent and len(data) > 1:
        lines = ["Time series data:"]
        prev_value = None
        for row in data:
            year = row.get('year', 'N/A')
            value = row.get('total_loss_ha', row.get('tree_cover_loss_ha', 0))
            
            # Calculate year-over-year change
            if prev_value is not None and prev_value > 0:
                change = ((value - prev_value) / prev_value) * 100
                lines.append(f"Year {year}: {value:,.0f} hectares ({change:+.1f}% YoY change)")
            else:
                lines.append(f"Year {year}: {value:,.0f} hectares")
            prev_value = value
            
        return "\n".join(lines)
    
    # For single results
    elif len(data) == 1:
        row = data[0]
        items = []
        for key, value in row.items():
            if isinstance(value, (int, float)):
                items.append(f"{key}: {value:,.0f}")
            else:
                items.append(f"{key}: {value}")
        return ", ".join(items)
    
    # For multiple results
    else:
        lines = []
        for row in data[:20]:  # Limit to 20 rows
            items = []
            for key, value in row.items():
                if isinstance(value, (int, float)):
                    items.append(f"{key}: {value:,.0f}")
                else:
                    items.append(f"{key}: {value}")
            lines.append(", ".join(items))
        return "\n".join(lines)

def extract_entities(question: str) -> Dict[str, Any]:
    """Extract entities from natural language question"""
    entities = {}
    question_lower = question.lower()
    
    # Extract country
    router_entities = query_router.extract_entities(question)
    if "country" in router_entities:
        entities["country"] = router_entities["country"]
    
    # Extract year (4-digit number between 2001-2024)
    year_match = re.search(r'\b(20[0-2][0-9])\b', question)
    if year_match:
        year = int(year_match.group(1))
        if 2001 <= year <= 2024:
            entities["year"] = year
    
    # Extract metric
    metric_mappings = {
        "deforestation": "tree_cover_loss_ha",
        "forest loss": "tree_cover_loss_ha",
        "tree loss": "tree_cover_loss_ha",
        "carbon": "carbon_emissions_mg_co2e",
        "emissions": "carbon_emissions_mg_co2e",
        "primary forest": "primary_forest_loss_ha",
        "virgin forest": "primary_forest_loss_ha"
    }
    
    for keyword, column in metric_mappings.items():
        if keyword in question_lower:
            entities["metric"] = column
            break
    
    # Set default threshold
    entities["threshold"] = 30
    
    logger.info(f"Extracted entities: {entities}")
    return entities

def detect_intent(question: str) -> str:
    """Detect query intent from question"""
    question_lower = question.lower()
    
    # Check for trend FIRST (most specific)
    if any(word in question_lower for word in ["trend", "trends", "over time", "historical", "trajectory", "evolution", "pattern", "change"]):
        logger.info("Intent detected: TREND")
        return "trend"
    
    # Check for percentage queries
    if any(word in question_lower for word in ["percentage", "percent", "%", "proportion", "fraction"]):
        if "primary" in question_lower or "virgin" in question_lower:
            return "primary_percentage"
    
    # Check for comparisons
    if any(word in question_lower for word in ["compare", "versus", "vs", "difference", "between"]):
        return "comparison"
    
    # Check for rankings
    if any(word in question_lower for word in ["top", "highest", "most", "ranking", "worst", "best"]):
        return "ranking"
    
    # Check for totals/aggregations
    if any(word in question_lower for word in ["total", "sum", "aggregate", "overall", "combined"]):
        return "aggregation"
    
    # Check for carbon intensity
    if "carbon" in question_lower and "intensity" in question_lower:
        return "carbon_intensity"
    
    # Default fallback
    logger.info("Intent detected: SIMPLE_METRIC (default)")
    return "simple_metric"

@app.list_tools()
async def list_tools() -> List[types.Tool]:
    """List available tools for Claude Desktop"""
    return [
        types.Tool(
            name="query_forest_data",
            description="Query Global Forest Watch deforestation data with climate analysis. Examples: 'What is Brazil's forest loss in 2023?', 'Show deforestation trend for Indonesia', 'Compare carbon emissions between countries'",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Natural language question about forest/climate data"
                    }
                },
                "required": ["question"]
            }
        ),
        types.Tool(
            name="get_trends",
            description="Analyze forest loss trends over time for specific countries or globally",
            inputSchema={
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "description": "Country to analyze (optional, default: global)"
                    },
                    "start_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024
                    },
                    "end_year": {
                        "type": "integer",
                        "minimum": 2001,
                        "maximum": 2024
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_data_summary",
            description="Get summary statistics about the forest database",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls from Claude Desktop"""
    
    try:
        if name == "query_forest_data":
            question = arguments.get("question", "")
            logger.info(f"=== QUERY_FOREST_DATA ===")
            logger.info(f"Processing query: {question}")
            
            # Extract entities and detect intent
            entities = extract_entities(question)
            intent = detect_intent(question)
            logger.info(f"Detected intent: {intent}")
            
            # Route query
            routing = query_router.route(question, **entities)
            routing = query_router.validate_routing(routing, **entities)
            
            # Generate SQL
            sql, params = sql_generator.generate(
                routing=routing,
                question=question,
                **entities
            )
            
            logger.info(f"SQL: {sql}")
            logger.info(f"Params: {params}")
            
            # Execute query
            results = query_executor.execute_query(sql, params)
            logger.info(f"Query returned {len(results)} results")
            
            # Format basic answer based on intent
            if not results:
                answer = "No data found for your query."
                final_answer = answer
            else:
                # Format data properly for different intents
                if intent == "trend" and len(results) > 1:
                    country = entities.get("country", "the region")
                    lines = [f"**Forest Loss Trend for {country}:**"]
                    for r in results:
                        year = r.get('year', 'N/A')
                        value = r.get('total_loss_ha', r.get('tree_cover_loss_ha', 0))
                        lines.append(f"• {year}: {value:,.0f} hectares")
                    answer = "\n".join(lines)
                    
                elif intent == "simple_metric" and len(results) == 1:
                    row = results[0]
                    value = row.get("tree_cover_loss_ha", 0)
                    country = entities.get("country", "the region")
                    year = entities.get("year", "the period")
                    answer = f"**Forest Data:** {country} in {year}: {value:,.0f} hectares"
                    
                else:
                    # Format first few results
                    lines = [f"**Query Results ({len(results)} records):**"]
                    for r in results[:5]:
                        items = []
                        for k, v in r.items():
                            if isinstance(v, (int, float)):
                                items.append(f"{k}: {v:,.0f}")
                            else:
                                items.append(f"{k}: {v}")
                        lines.append("• " + ", ".join(items))
                    answer = "\n".join(lines)
                
                # ALWAYS call ClimateGPT when we have data
                logger.info("Calling ClimateGPT for enhanced analysis...")
                climate_analysis = await call_climategpt(
                    question=question,
                    data=results,
                    metadata={
                        "intent": intent,
                        "entities": entities,
                        "tables": [t.value for t in routing.tables]
                    }
                )
                
                # Combine data and climate analysis
                if climate_analysis:
                    final_answer = f"{answer}\n\n**🌍 Climate Analysis:**\n{climate_analysis}"
                else:
                    final_answer = answer
                
                # Add metadata
                metadata_text = f"\n\n📊 *Query: Intent={intent}, Rows={len(results)}*"
                final_answer += metadata_text
            
            return [types.TextContent(
                type="text",
                text=final_answer
            )]
            
        elif name == "get_trends":
            logger.info("=== GET_TRENDS ===")
            country = arguments.get("country")
            start_year = arguments.get("start_year", 2001)
            end_year = arguments.get("end_year", 2024)
            
            logger.info(f"Getting trends for {country or 'Global'} from {start_year} to {end_year}")
            
            # Build SQL for trend analysis
            sql = """
                SELECT year, SUM(tree_cover_loss_ha) as total_loss_ha
                FROM fact_tree_cover_loss
                WHERE threshold = 30
                    AND year BETWEEN ? AND ?
            """
            params = [start_year, end_year]
            
            if country:
                sql += " AND country = ?"
                params.append(country)
                
            sql += " GROUP BY year ORDER BY year"
            
            results = query_executor.execute_query(sql, tuple(params))
            logger.info(f"Trend query returned {len(results)} years of data")
            
            if results:
                text = f"**Forest Loss Trends"
                if country:
                    text += f" for {country}"
                text += f" ({start_year}-{end_year}):**\n\n"
                
                for row in results:
                    text += f"• {row['year']}: {row['total_loss_ha']:,.0f} hectares\n"
                    
                # Calculate overall change
                if len(results) > 1:
                    first_year = results[0]['total_loss_ha']
                    last_year = results[-1]['total_loss_ha']
                    change = ((last_year - first_year) / first_year) * 100
                    text += f"\n**Overall change: {change:+.1f}%**"
                
                # Get climate analysis for trends
                logger.info("Getting ClimateGPT analysis for trend data...")
                climate_analysis = await call_climategpt(
                    question=f"Analyze the deforestation trend for {country or 'global'} from {start_year} to {end_year}",
                    data=results,
                    metadata={"intent": "trend", "entities": {"country": country}}
                )
                
                if climate_analysis:
                    text += f"\n\n**🌍 Climate Analysis:**\n{climate_analysis}"
            else:
                text = "No trend data available for the specified parameters."
                
            return [types.TextContent(type="text", text=text)]
            
        elif name == "get_data_summary":
            logger.info("=== GET_DATA_SUMMARY ===")
            # Get database statistics
            stats = {}
            tables = ["fact_tree_cover_loss", "fact_primary_forest", "fact_carbon"]
            
            for table in tables:
                sql = f"SELECT COUNT(*) as count FROM {table}"
                result = query_executor.execute_query(sql)
                if result:
                    stats[table] = result[0]["count"]
            
            summary = "**Forest Database Summary:**\n"
            summary += f"• Tree Cover Loss: {stats.get('fact_tree_cover_loss', 0):,} records\n"
            summary += f"• Primary Forest: {stats.get('fact_primary_forest', 0):,} records\n"
            summary += f"• Carbon Emissions: {stats.get('fact_carbon', 0):,} records\n"
            summary += "\nData covers 165 countries from 2001-2024"
            
            return [types.TextContent(type="text", text=summary)]
            
        else:
            return [types.TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]
            
    except Exception as e:
        logger.error(f"Tool execution error: {e}", exc_info=True)
        return [types.TextContent(
            type="text",
            text=f"Error processing request: {str(e)}"
        )]

async def main():
    """Run the MCP server with stdio transport"""
    logger.info("Starting Forest-Climate MCP Server with ClimateGPT integration...")
    
    # Verify database exists
    if not Path(DATABASE_PATH).exists():
        logger.error(f"Database not found at {DATABASE_PATH}")
        sys.exit(1)
    
    logger.info(f"Using database: {DATABASE_PATH}")
    logger.info(f"ClimateGPT endpoint: {CLIMATEGPT_URL}")
    
    # Run with stdio transport
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="forest-climate-analyzer",
                server_version="1.0.0",
                capabilities={}
            )
        )

if __name__ == "__main__":
    asyncio.run(main())