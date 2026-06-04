"""
Forest Climate Analyzer - live demo
Interactive view over Global Forest Watch deforestation, primary-forest, and
forest-carbon data (165 countries, 2,779 regions, 2001-2024).

This is the public demo companion to the Nexus-MCP capstone project: a
Model Context Protocol server that lets an LLM query this data in natural
language. The demo runs the same SQLite analytics layer behind a UI.
"""
import os
import sqlite3
import pandas as pd
import streamlit as st

DB_PATH = os.getenv("DEMO_DB", os.path.join(os.path.dirname(__file__), "data", "forest_demo.db"))
SOURCE = "Hansen/UMD/Google/USGS/NASA - Global Forest Watch (tree cover); GFW forest-carbon flux."

st.set_page_config(page_title="Forest Climate Analyzer", page_icon="🌳", layout="wide")


@st.cache_resource
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data
def q(sql, params=()):
    return pd.read_sql_query(sql, get_conn(), params=params)


@st.cache_data
def countries():
    return q("SELECT DISTINCT country FROM fact_tree_cover_loss ORDER BY country")["country"].tolist()


# ---------------- Header ----------------
st.title("🌳 Forest Climate Analyzer")
st.caption(
    "Interactive analytics over 24 years of Global Forest Watch data - "
    "tree cover loss, primary-forest loss, and forest-carbon emissions across "
    "165 countries and 2,779 subnational regions."
)

# ---------------- Sidebar controls ----------------
with st.sidebar:
    st.header("Filters")
    country = st.selectbox("Country", countries(), index=countries().index("Brazil") if "Brazil" in countries() else 0)
    years = q("SELECT DISTINCT year FROM fact_tree_cover_loss ORDER BY year")["year"].tolist()
    year = st.slider("Year", min(years), max(years), max(years))
    threshold = st.selectbox(
        "Canopy density threshold (%)", [10, 15, 20, 25, 30, 50, 75],
        index=4, help="30% is the FAO forest standard.")
    st.markdown("---")
    st.markdown("**Data source**")
    st.caption(SOURCE)

# ---------------- KPI row ----------------
tcl = q("SELECT tree_cover_loss_ha, loss_rate_pct FROM fact_tree_cover_loss WHERE country=? AND year=? AND threshold=?",
        (country, year, threshold))
pf = q("SELECT primary_forest_loss_ha FROM fact_primary_forest WHERE country=? AND year=?", (country, year))
carb_thr = threshold if threshold in (30, 50, 75) else 30
carb = q("SELECT carbon_emissions_mg_co2e FROM fact_carbon WHERE country=? AND year=? AND threshold=?",
         (country, year, carb_thr))

c1, c2, c3 = st.columns(3)
c1.metric(f"Tree cover loss ({year})",
          f"{tcl['tree_cover_loss_ha'].iloc[0]:,.0f} ha" if len(tcl) else "n/a",
          help=f"At {threshold}% canopy density")
c2.metric(f"Primary forest loss ({year})",
          f"{pf['primary_forest_loss_ha'].iloc[0]:,.0f} ha" if len(pf) and pd.notna(pf['primary_forest_loss_ha'].iloc[0]) else "n/a (tropical only)")
c3.metric(f"Forest carbon emissions ({year})",
          f"{carb['carbon_emissions_mg_co2e'].iloc[0]:,.0f} Mg CO2e" if len(carb) else "n/a",
          help=f"At {carb_thr}% threshold")

st.markdown("---")

# ---------------- Trend + ranking ----------------
left, right = st.columns(2)

with left:
    st.subheader(f"{country} - tree cover loss over time")
    trend = q("SELECT year, tree_cover_loss_ha FROM fact_tree_cover_loss WHERE country=? AND threshold=? ORDER BY year",
              (country, threshold))
    if len(trend):
        st.line_chart(trend.set_index("year"))
    else:
        st.info("No trend data.")

with right:
    st.subheader(f"Top 10 countries by loss ({year}, {threshold}%)")
    rank = q("""SELECT country, tree_cover_loss_ha FROM fact_tree_cover_loss
                WHERE year=? AND threshold=? ORDER BY tree_cover_loss_ha DESC LIMIT 10""",
             (year, threshold))
    st.bar_chart(rank.set_index("country"))

# ---------------- Subnational ----------------
st.subheader(f"Top subnational regions in {country} ({year}, 30% standard)")
sub = q("""SELECT subnational1 AS region, tree_cover_loss_ha AS loss_ha
           FROM fact_subnational_tree_cover_loss
           WHERE country=? AND year=? AND threshold=30
           ORDER BY tree_cover_loss_ha DESC LIMIT 12""", (country, year))
if len(sub):
    st.bar_chart(sub.set_index("region"))
    with st.expander("View as table"):
        st.dataframe(sub.style.format({"loss_ha": "{:,.0f}"}), use_container_width=True)
else:
    st.info(f"No subnational data for {country} in {year}.")

# ---------------- Optional LLM narrative ----------------
st.markdown("---")
st.subheader("AI analysis (optional)")
api_key = os.getenv("LLM_API_KEY") or st.secrets.get("LLM_API_KEY", "") if hasattr(st, "secrets") else os.getenv("LLM_API_KEY", "")
if st.button("Generate analyst summary"):
    if not api_key:
        st.warning("Set LLM_API_KEY (a free Groq key) in app secrets to enable AI narratives.")
    else:
        import httpx
        ctx = trend.tail(10).to_dict("records") if len(trend) else []
        prompt = (f"In under 150 words, summarize forest loss trends for {country} "
                  f"at {threshold}% canopy. Recent annual tree cover loss (ha): {ctx}. "
                  "Note risks and one actionable insight. Format numbers with commas.")
        try:
            r = httpx.post(
                os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 300, "temperature": 0.6},
                timeout=30.0)
            st.write(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            st.error(f"LLM call failed: {e}")

st.markdown("---")
st.caption(f"Source: {SOURCE}  |  Demo of the Nexus-MCP capstone (GMU DAEN).")
