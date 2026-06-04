"""
Query router that determines which tables and SQL patterns to use.
"""
import logging
import re
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from nexus.data.metadata.metadata_manager import metadata_manager

from nexus.mcp.models import RoutingDecision, QueryIntent, TableType
from nexus.config.constants import (
    TROPICAL_COUNTRIES,
    CARBON_THRESHOLDS,
    PRIMARY_THRESHOLD,
    NL_COLUMN_MAPPINGS,
    QUERY_PATTERNS,
)

logger = logging.getLogger(__name__)


class QueryRouter:
    """Routes natural language queries to appropriate tables and SQL patterns."""
    
    def __init__(self, semantic_metadata: Optional[Dict] = None):
        """
        Initialize query router.
        
        Args:
            semantic_metadata: Optional semantic metadata for enhanced routing
        """
        self.metadata = metadata_manager
        self.keyword_patterns = self._compile_keyword_patterns()
        self.country_mappings = self._build_country_mappings()
        
    def _compile_keyword_patterns(self) -> Dict[str, re.Pattern]:
        """Compile regex patterns for keyword detection."""
        return {
            "primary": re.compile(r'\b(primary|virgin|pristine|untouched|old[\s-]?growth)\b', re.I),
            "carbon": re.compile(r'\b(carbon|co2|emissions|greenhouse|sequestration)\b', re.I),
            "intensity": re.compile(r'\b(intensity|per[\s-]?hectare|density)\b', re.I),
            "trend": re.compile(r'\b(trend|over[\s-]?time|trajectory|evolution|change|historical|pattern)\b', re.I),
            "comparison": re.compile(r'\b(compare|versus|vs\.?|difference|between)\b', re.I),
            "ranking": re.compile(r'\b(top|most|least|highest|lowest|rank|leading|worst|best)\b', re.I),
            "percentage": re.compile(r'\b(percentage|percent|%|proportion|share|fraction)\b', re.I),
            "total": re.compile(r'\b(total|sum|aggregate|overall|combined|cumulative)\b', re.I),
        }
    
    def _build_country_mappings(self) -> Dict[str, str]:
        """
        Build comprehensive country name mappings including aliases and abbreviations.
        Returns a dictionary mapping various forms to canonical country names.
        """
        # Define comprehensive country list with their aliases
        country_data = {
            "United States": ["us", "usa", "united states", "america", "united states of america", "u.s.", "u.s.a."],
            "United Kingdom": ["uk", "britain", "great britain", "england", "united kingdom", "u.k."],
            "Brazil": ["brazil", "brasil"],
            "Indonesia": ["indonesia"],
            "Democratic Republic of the Congo": ["drc", "dr congo", "democratic republic of congo", 
                                                  "congo kinshasa", "congo-kinshasa", "dem rep congo",
                                                  "democratic republic of the congo"],
            "Russia": ["russia", "russian federation"],
            "Canada": ["canada"],
            "China": ["china", "prc", "people's republic of china", "mainland china"],
            "India": ["india"],
            "Peru": ["peru"],
            "Colombia": ["colombia"],
            "Bolivia": ["bolivia"],
            "Venezuela": ["venezuela"],
            "Malaysia": ["malaysia"],
            "Mexico": ["mexico"],
            "Argentina": ["argentina"],
            "Madagascar": ["madagascar"],
            "Australia": ["australia", "aus"],
            "Myanmar": ["myanmar", "burma"],
            "Tanzania": ["tanzania"],
            "Zambia": ["zambia"],
            "Papua New Guinea": ["papua new guinea", "png"],
            "Angola": ["angola"],
            "Paraguay": ["paraguay"],
            "Mozambique": ["mozambique"],
            "Laos": ["laos", "lao"],
            "Cambodia": ["cambodia"],
            "Central African Republic": ["car", "central african republic"],
            "Cameroon": ["cameroon"],
            "Chile": ["chile"],
            "Thailand": ["thailand"],
            "Ecuador": ["ecuador"],
            "Japan": ["japan"],
            "Germany": ["germany", "deutschland"],
            "France": ["france"],
            "Spain": ["spain", "españa"],
            "Italy": ["italy", "italia"],
            "Poland": ["poland"],
            "Ukraine": ["ukraine"],
            "Sweden": ["sweden"],
            "Norway": ["norway"],
            "Finland": ["finland"],
            "New Zealand": ["new zealand", "nz"],
            "South Africa": ["south africa", "rsa"],
            "Nigeria": ["nigeria"],
            "Kenya": ["kenya"],
            "Ethiopia": ["ethiopia"],
            "Ghana": ["ghana"],
            "Ivory Coast": ["ivory coast", "cote d'ivoire", "côte d'ivoire"],
            "Gabon": ["gabon"],
            "Republic of the Congo": ["republic of congo", "congo brazzaville", "congo-brazzaville"],
            "Liberia": ["liberia"],
            "Guinea": ["guinea"],
            "Sierra Leone": ["sierra leone"],
            "Vietnam": ["vietnam", "viet nam"],
            "Philippines": ["philippines"],
            "South Korea": ["south korea", "korea", "republic of korea", "rok"],
            "North Korea": ["north korea", "dprk", "democratic people's republic of korea"],
            "Turkey": ["turkey", "türkiye", "turkiye"],
            "Pakistan": ["pakistan"],
            "Bangladesh": ["bangladesh"],
            "Nepal": ["nepal"],
            "Sri Lanka": ["sri lanka"],
            "Costa Rica": ["costa rica"],
            "Panama": ["panama"],
            "Nicaragua": ["nicaragua"],
            "Honduras": ["honduras"],
            "Guatemala": ["guatemala"],
            "Belize": ["belize"],
            "Uruguay": ["uruguay"],
            "Suriname": ["suriname"],
            "Guyana": ["guyana"],
            "French Guiana": ["french guiana"],
        }
        
        # Create reverse mapping (alias -> canonical name)
        mappings = {}
        for canonical_name, aliases in country_data.items():
            for alias in aliases:
                mappings[alias.lower()] = canonical_name
                
        return mappings
        
    def route(self, question: str, **kwargs) -> RoutingDecision:
        """
        Determine which tables and joins are needed for the query.
        
        Args:
            question: Natural language question
            **kwargs: Additional context (year, country, threshold)
            
        Returns:
            RoutingDecision with table selection and join requirements
        """
        question_lower = question.lower()
        
        # Detect query intent
        intent = self._detect_intent(question_lower)
        
        # Check for primary forest keywords
        if self.keyword_patterns["primary"].search(question_lower):
            if self.keyword_patterns["percentage"].search(question_lower):
                # Primary percentage query - needs join
                return RoutingDecision(
                    tables=[TableType.TREE_COVER, TableType.PRIMARY_FOREST],
                    primary_table=TableType.PRIMARY_FOREST,
                    requires_join=True,
                    join_conditions=[
                        "t.country = p.country",
                        "t.year = p.year"
                    ],
                    filters={"threshold": PRIMARY_THRESHOLD},
                    confidence=0.92,
                    intent=QueryIntent.PRIMARY_PERCENTAGE
                )
            else:
                # Simple primary forest query
                return RoutingDecision(
                    tables=[TableType.PRIMARY_FOREST],
                    primary_table=TableType.PRIMARY_FOREST,
                    requires_join=False,
                    join_conditions=None,
                    filters={"threshold": PRIMARY_THRESHOLD},
                    confidence=0.90,
                    intent=intent
                )
                
        # Check for carbon keywords
        elif self.keyword_patterns["carbon"].search(question_lower):
            if self.keyword_patterns["intensity"].search(question_lower):
                # Carbon intensity - needs join with tree cover
                return RoutingDecision(
                    tables=[TableType.TREE_COVER, TableType.CARBON],
                    primary_table=TableType.CARBON,
                    requires_join=True,
                    join_conditions=[
                        "t.country = c.country",
                        "t.year = c.year",
                        "t.threshold = c.threshold"
                    ],
                    filters={"threshold": CARBON_THRESHOLDS},
                    confidence=0.90,
                    intent=QueryIntent.CARBON_INTENSITY
                )
            else:
                # Simple carbon query
                return RoutingDecision(
                    tables=[TableType.CARBON],
                    primary_table=TableType.CARBON,
                    requires_join=False,
                    join_conditions=None,
                    filters={"threshold": CARBON_THRESHOLDS},
                    confidence=0.88,
                    intent=intent
                )
                
        # Default to tree cover loss
        else:
            # Apply threshold filter from kwargs if provided
            threshold = kwargs.get("threshold", PRIMARY_THRESHOLD)
            
            return RoutingDecision(
                tables=[TableType.TREE_COVER],
                primary_table=TableType.TREE_COVER,
                requires_join=False,
                join_conditions=None,
                filters={"threshold": threshold},
                confidence=0.95,
                intent=intent
            )
            
    def _detect_intent(self, question: str) -> QueryIntent:
        """Detect the intent of the query."""
        if self.keyword_patterns["trend"].search(question):
            return QueryIntent.TREND
        elif self.keyword_patterns["comparison"].search(question):
            return QueryIntent.COMPARISON
        elif self.keyword_patterns["ranking"].search(question):
            return QueryIntent.RANKING
        elif self.keyword_patterns["total"].search(question):
            return QueryIntent.AGGREGATION
        else:
            return QueryIntent.SIMPLE_METRIC
            
    def extract_entities(self, question: str) -> Dict[str, Any]:
        """
        Extract entities (country, year, threshold) from natural language.
        Enhanced to handle country aliases, abbreviations, and common variations.
        
        Args:
            question: Natural language question
            
        Returns:
            Dictionary of extracted entities
        """
        entities = {}
        question_lower = question.lower()
        
        # Extract year (4-digit number between 2001-2024)
        year_patterns = [
            r'\b(20[0-2][0-9])\b',  # Standard 4-digit year
            r'year\s+(\d{4})',       # "year 2023"
            r'in\s+(\d{4})',         # "in 2023"
            r'for\s+(\d{4})',        # "for 2023"
            r'during\s+(\d{4})',     # "during 2023"
        ]
        
        for pattern in year_patterns:
            year_match = re.search(pattern, question, re.I)
            if year_match:
                year = int(year_match.group(1))
                if 2001 <= year <= 2024:
                    entities["year"] = year
                    break
                
        # Extract threshold percentages
        threshold_patterns = [
            r'\b(\d{1,2})\s*%\s*(?:threshold)?',  # "30% threshold" or "30%"
            r'\b(\d{1,2})\s*percent\s*(?:threshold)?',  # "30 percent threshold"
            r'threshold\s*(?:of)?\s*(\d{1,2})',  # "threshold of 30"
            r'canopy\s*(?:cover)?\s*(?:of)?\s*(\d{1,2})',  # "canopy cover of 30"
        ]
        
        for pattern in threshold_patterns:
            threshold_match = re.search(pattern, question, re.I)
            if threshold_match:
                threshold = int(threshold_match.group(1))
                if threshold in [0, 10, 15, 20, 25, 30, 50, 75]:
                    entities["threshold"] = threshold
                    break
                    
        # Extract country using comprehensive mappings
        # First, try to find exact matches for multi-word country names
        multi_word_countries = [
            "democratic republic of the congo",
            "united states",
            "united kingdom",
            "papua new guinea",
            "central african republic",
            "south africa",
            "new zealand",
            "south korea",
            "north korea",
            "costa rica",
            "sri lanka",
            "sierra leone",
            "ivory coast",
            "republic of the congo",
            "french guiana"
        ]
        
        # Check for multi-word countries first (to avoid partial matches)
        for multi_word in multi_word_countries:
            if multi_word in question_lower:
                if multi_word in self.country_mappings:
                    entities["country"] = self.country_mappings[multi_word]
                    logger.debug(f"Matched multi-word country: {multi_word} -> {entities['country']}")
                    return entities  # Return early if we found a multi-word match
        
        # Now check for any country name or alias
        # Sort by length (longest first) to avoid partial matches
        sorted_aliases = sorted(self.country_mappings.keys(), key=len, reverse=True)
        
        for alias in sorted_aliases:
            # Use word boundaries for single-word countries to avoid partial matches
            if len(alias.split()) == 1:
                # For single words, use word boundary matching
                pattern = r'\b' + re.escape(alias) + r'\b'
                if re.search(pattern, question_lower, re.I):
                    entities["country"] = self.country_mappings[alias]
                    logger.debug(f"Matched country alias: {alias} -> {entities['country']}")
                    break
            else:
                # For multi-word, we already checked above
                continue
        
        # Log what we extracted for debugging
        if entities:
            logger.info(f"Extracted entities from '{question}': {entities}")
        else:
            logger.warning(f"No entities extracted from: '{question}'")
                
        return entities
        
    def validate_routing(self, routing: RoutingDecision, **kwargs) -> RoutingDecision:
        """
        Validate and adjust routing based on business rules.
        
        Args:
            routing: Initial routing decision
            **kwargs: Additional context
            
        Returns:
            Validated routing decision
        """
        # Check if country is tropical for primary forest queries
        if TableType.PRIMARY_FOREST in routing.tables:
            country = kwargs.get("country")
            if country and country not in TROPICAL_COUNTRIES:
                logger.warning(
                    f"Primary forest data requested for non-tropical country: {country}"
                )
                # Remove primary forest from routing
                routing.tables = [TableType.TREE_COVER]
                routing.primary_table = TableType.TREE_COVER
                routing.requires_join = False
                routing.join_conditions = None
                routing.confidence *= 0.8
                
        # Validate carbon thresholds
        if TableType.CARBON in routing.tables:
            threshold = kwargs.get("threshold")
            if threshold and threshold not in CARBON_THRESHOLDS:
                logger.warning(
                    f"Carbon data requested for invalid threshold: {threshold}"
                )
                # Adjust to nearest valid threshold
                valid_thresholds = CARBON_THRESHOLDS
                nearest = min(valid_thresholds, key=lambda x: abs(x - threshold))
                routing.filters["threshold"] = nearest
                routing.confidence *= 0.9
                
        return routing
        
    def get_confidence_factors(self, question: str, routing: RoutingDecision) -> Dict[str, float]:
        """
        Calculate confidence factors for the routing decision.
        
        Returns:
            Dictionary of confidence factors
        """
        factors = {
            "keyword_match": 0.0,
            "entity_extraction": 0.0,
            "pattern_match": 0.0,
            "table_selection": 0.0,
        }
        
        # Check keyword matches
        matched_keywords = sum(
            1 for pattern in self.keyword_patterns.values()
            if pattern.search(question)
        )
        factors["keyword_match"] = min(matched_keywords / 3, 1.0)
        
        # Check entity extraction
        entities = self.extract_entities(question)
        factors["entity_extraction"] = len(entities) / 3  # year, country, threshold
        
        # Check pattern matches
        for pattern_type in QUERY_PATTERNS:
            if any(keyword in question.lower() for keyword in QUERY_PATTERNS[pattern_type]):
                factors["pattern_match"] = 1.0
                break
                
        # Check table selection confidence
        if routing.requires_join:
            factors["table_selection"] = 0.8  # Joins are more complex
        else:
            factors["table_selection"] = 1.0
            
        return factors