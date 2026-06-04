# Tree cover thresholds
ALL_THRESHOLDS = [0, 10, 15, 20, 25, 30, 50, 75]
CARBON_THRESHOLDS = [30, 50, 75]  # Only these have carbon data
PRIMARY_THRESHOLD = 30  # Primary forest is always at 30%
FAO_STANDARD_THRESHOLD = 30

# Year ranges
TREE_COVER_YEARS = range(2001, 2025)  # 2001-2024
PRIMARY_FOREST_YEARS = range(2002, 2025)  # 2002-2024
CARBON_YEARS = range(2001, 2025)  # 2001-2024

# Tropical countries (75 total)
TROPICAL_COUNTRIES = {
    "Angola", "Argentina", "Australia", "Bangladesh", "Benin", "Bhutan",
    "Bolivia", "Brazil", "Brunei", "Burundi", "Cambodia", "Cameroon",
    "Central African Republic", "China", "Colombia", "Costa Rica",
    "Côte d'Ivoire", "Cuba", "Democratic Republic of the Congo",
    "Dominican Republic", "Ecuador", "El Salvador", "Equatorial Guinea",
    "Ethiopia", "Fiji", "French Guiana", "Gabon", "Ghana", "Guadeloupe", "Guatemala",
    "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras", "India",
    "Indonesia", "Kenya", "Laos", "Liberia", "Madagascar", "Malawi",
    "Malaysia", "Martinique", "México", "Mozambique", "Myanmar", "Nepal",
    "Nicaragua", "Nigeria", "Panama", "Papua New Guinea", "Paraguay",
    "Peru", "Philippines", "Republic of the Congo", "Rwanda", "Senegal",
    "Sierra Leone", "Solomon Islands", "South Africa", "South Sudan",
    "Sri Lanka", "Suriname", "Tanzania", "Thailand", "Togo", "Uganda",
    "United States", "Vanuatu", "Venezuela", "Vietnam", "Virgin Islands, U.S.", "Zambia", "Zimbabwe"
}

SQL_TEMPLATES = {
    "primary_percentage": """
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
        WHERE t.threshold = 30 {additional_conditions}
    """,
    "carbon_intensity": """
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
        WHERE 1=1 {additional_conditions}
    """,
    "trend_analysis": """
        SELECT 
            year,
            SUM(tree_cover_loss_ha) as total_loss_ha
        FROM fact_tree_cover_loss
        WHERE threshold = {threshold} {additional_conditions}
        GROUP BY year
        ORDER BY year
    """,
    "country_ranking": """
        SELECT 
            country,
            SUM(tree_cover_loss_ha) as total_loss_ha
        FROM fact_tree_cover_loss
        WHERE threshold = {threshold}
            AND year BETWEEN {start_year} AND {end_year}
        GROUP BY country
        ORDER BY total_loss_ha DESC
        LIMIT {limit}
    """
}
EXPECTED_ROWS = {
    "fact_tree_cover_loss": 31680,
    "fact_primary_forest": 1725,
    "fact_carbon": 11880
}
NL_COLUMN_MAPPINGS = {
    "forest loss": "tree_cover_loss_ha",
    "primary forest": "primary_forest_loss_ha",
    "carbon emissions": "carbon_emissions_mg_co2e",
    "intensity": "carbon_per_hectare",
    "percentage": "primary_percentage"
}
QUERY_PATTERNS = {
    "primary_percentage": ["percentage of primary", "proportion of virgin forest"],
    "carbon_intensity": ["carbon intensity", "emissions per hectare"],
    "trend": ["trend", "over time"],
    "ranking": ["top", "highest", "most"],
    "comparison": ["compare", "versus"],
    "aggregation": ["total", "sum"]
}