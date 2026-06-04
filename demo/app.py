"""
Forest Climate Analyzer - natural-language demo.

Ask a question in plain English ("How much forest did Brazil lose in 2023?",
"Which Indonesian states emit the most carbon?") and get a grounded answer in
plain English. This mirrors the MCP server: a rule-based router extracts intent
+ entities, runs parameterized SQL over the warehouse, and an LLM phrases the
answer from ONLY the retrieved rows (no fabrication). Without an LLM key it
falls back to a templated natural-language answer.
"""
import os
import re
import sqlite3
import pandas as pd
import streamlit as st

DB_PATH = os.getenv("DEMO_DB", os.path.join(os.path.dirname(__file__), "data", "forest_demo.db"))
SOURCE = "Hansen/UMD/Google/USGS/NASA - Global Forest Watch; GFW forest-carbon flux."

st.set_page_config(page_title="Forest Climate Analyzer", page_icon="🌳", layout="centered")


@st.cache_resource
def conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data
def q(sql, params=()):
    return pd.read_sql_query(sql, conn(), params=params)


@st.cache_data
def country_list():
    return q("SELECT DISTINCT country FROM fact_tree_cover_loss ORDER BY country")["country"].tolist()


# --------------------------------------------------------------------------
# NL understanding (compact port of the project's router: aliases + intent)
# --------------------------------------------------------------------------
ALIASES = {
    "united states": "United States", "usa": "United States", "us": "United States", "america": "United States",
    "uk": "United Kingdom", "britain": "United Kingdom",
    "drc": "Democratic Republic of the Congo", "dr congo": "Democratic Republic of the Congo",
    "democratic republic of the congo": "Democratic Republic of the Congo",
    "republic of the congo": "Republic of the Congo",
    "png": "Papua New Guinea", "papua new guinea": "Papua New Guinea",
    "ivory coast": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire",
    "car": "Central African Republic", "burma": "Myanmar",
}


def detect_country(text):
    t = text.lower()
    # multiword aliases / names first
    for alias, canon in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(alias) + r"\b", t):
            return canon
    # then real DB names (longest first to avoid partial hits)
    for c in sorted(country_list(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(c.lower()) + r"\b", t):
            return c
    return None


def detect_year(text):
    m = re.findall(r"\b(20[0-2]\d)\b", text)
    yrs = [int(x) for x in m if 2001 <= int(x) <= 2024]
    return yrs[-1] if yrs else None


def detect_threshold(text):
    m = re.search(r"\b(\d{1,2})\s*%", text)
    if m and int(m.group(1)) in (0, 10, 15, 20, 25, 30, 50, 75):
        return int(m.group(1))
    return 30


def parse(text):
    t = text.lower()
    e = {
        "country": detect_country(text),
        "year": detect_year(text),
        "threshold": detect_threshold(text),
    }
    if re.search(r"\b(carbon|co2|emission|emissions|greenhouse)\b", t):
        e["metric"] = "carbon"
    elif re.search(r"\b(primary|virgin|old[\s-]?growth|pristine)\b", t):
        e["metric"] = "primary"
    else:
        e["metric"] = "tree_cover"
    if re.search(r"\b(state|states|province|provinces|region|regions|subnational)\b", t):
        e["scope"] = "subnational"
    elif re.search(r"\b(top|most|highest|worst|rank|leading|biggest)\b", t):
        e["scope"] = "rank"
    elif re.search(r"\b(trend|over time|since|history|historical|each year|by year)\b", t):
        e["scope"] = "trend"
    else:
        e["scope"] = "single"
    return e


def fnum(v):
    try:
        return f"{float(v):,.0f}"
    except Exception:
        return str(v)


# --------------------------------------------------------------------------
# Retrieval: entities -> SQL -> rows  (parameterized)
# --------------------------------------------------------------------------
def retrieve(e):
    metric, scope = e["metric"], e["scope"]
    year = e["year"]
    thr = 30 if metric != "tree_cover" else e["threshold"]
    val = {"tree_cover": "tree_cover_loss_ha", "carbon": "carbon_emissions_mg_co2e",
           "primary": "primary_forest_loss_ha"}[metric]
    unit = {"tree_cover": "ha", "carbon": "Mg CO₂e", "primary": "ha"}[metric]
    tbl_country = {"tree_cover": "fact_tree_cover_loss", "carbon": "fact_carbon",
                   "primary": "fact_primary_forest"}[metric]
    tbl_sub = {"tree_cover": "fact_subnational_tree_cover_loss",
               "carbon": "fact_subnational_carbon",
               "primary": "fact_subnational_primary_forest"}[metric]
    has_thr = metric != "primary"

    if scope == "subnational" and e["country"]:
        y = year or 2023
        where = "country=? AND year=?" + (" AND threshold=30" if has_thr else "")
        rows = q(f"SELECT subnational1 AS region, {val} AS value FROM {tbl_sub} "
                 f"WHERE {where} ORDER BY {val} DESC LIMIT 10", (e["country"], y))
        return rows, val, unit, {"scope": "subnational", "year": y}

    if scope == "rank":
        y1, y2 = (year, year) if year else (2020, 2024)
        where = "year BETWEEN ? AND ?" + (" AND threshold=?" if has_thr else "")
        params = (y1, y2, thr) if has_thr else (y1, y2)
        rows = q(f"SELECT country, SUM({val}) AS value FROM {tbl_country} "
                 f"WHERE {where} GROUP BY country ORDER BY value DESC LIMIT 10", params)
        return rows, val, unit, {"scope": "rank", "y1": y1, "y2": y2}

    if scope == "trend" and e["country"]:
        where = "country=?" + (" AND threshold=?" if has_thr else "")
        params = (e["country"], thr) if has_thr else (e["country"],)
        rows = q(f"SELECT year, {val} AS value FROM {tbl_country} WHERE {where} ORDER BY year", params)
        return rows, val, unit, {"scope": "trend"}

    # single metric
    if e["country"]:
        y = year or 2023
        where = "country=? AND year=?" + (" AND threshold=?" if has_thr else "")
        params = (e["country"], y, thr) if has_thr else (e["country"], y)
        rows = q(f"SELECT {val} AS value FROM {tbl_country} WHERE {where}", params)
        return rows, val, unit, {"scope": "single", "year": y, "threshold": thr}
    return pd.DataFrame(), val, unit, {"scope": "none"}


def templated_answer(text, e, rows, val, unit, meta):
    if rows.empty:
        if not e["country"] and meta.get("scope") not in ("rank",):
            return "I couldn't find a country in your question. Try e.g. *\"forest loss in Brazil in 2023\"* or *\"top carbon-emitting countries\"*."
        return "No data found for that combination. Try a different year (2001–2024) or country."
    m = {"tree_cover": "tree cover loss", "carbon": "forest-carbon emissions", "primary": "primary-forest loss"}[e["metric"]]
    s = meta["scope"]
    if s == "single":
        return f"**{e['country']}** had **{fnum(rows['value'].iloc[0])} {unit}** of {m} in **{meta['year']}**" + (f" (at {meta['threshold']}% canopy)." if e['metric']=='tree_cover' else ".")
    if s == "rank":
        lines = [f"{i+1}. {r['country']} — {fnum(r['value'])} {unit}" for i, r in rows.iterrows()]
        return f"**Top countries by {m} ({meta['y1']}–{meta['y2']}):**\n\n" + "\n".join(lines)
    if s == "subnational":
        lines = [f"{i+1}. {r['region']} — {fnum(r['value'])} {unit}" for i, r in rows.iterrows()]
        return f"**Top regions in {e['country']} by {m} ({meta['year']}):**\n\n" + "\n".join(lines)
    if s == "trend":
        tot = rows["value"].sum()
        peak = rows.loc[rows["value"].idxmax()]
        return f"**{e['country']}** {m} totaled **{fnum(tot)} {unit}** across {len(rows)} years; the worst year was **{int(peak['year'])}** ({fnum(peak['value'])} {unit})."
    return "Here's what I found."


def llm_answer(text, rows, meta):
    key = os.getenv("LLM_API_KEY") or (st.secrets.get("LLM_API_KEY", "") if hasattr(st, "secrets") else "")
    if not key or rows.empty:
        return None
    import httpx
    data = rows.head(12).to_dict("records")
    sys = ("You answer questions about Global Forest Watch deforestation/carbon data. "
           "Use ONLY the data provided - never invent numbers. Be concise (under 120 words), "
           "format numbers with commas, and write naturally for a non-expert.")
    usr = f"Question: {text}\n\nData (rows): {data}\n\nAnswer the question using only this data."
    try:
        r = httpx.post(os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"),
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json={"model": os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
                             "messages": [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                             "max_tokens": 300, "temperature": 0.4}, timeout=30.0)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
st.title("🌳 Forest Climate Analyzer")
st.caption("Ask in plain English about 24 years of Global Forest Watch data — "
           "165 countries, 2,779 regions. Tree cover loss, primary-forest loss, forest-carbon emissions.")

examples = [
    "How much forest did Brazil lose in 2023?",
    "Top 10 countries for tree cover loss since 2020",
    "Which states in Indonesia emit the most carbon?",
    "Primary forest loss trend for DR Congo",
]
st.write("**Try:**")
cols = st.columns(2)
clicked = None
for i, ex in enumerate(examples):
    if cols[i % 2].button(ex, use_container_width=True):
        clicked = ex

if "history" not in st.session_state:
    st.session_state.history = []

prompt = st.chat_input("Ask a question about forest loss or carbon...") or clicked

for role, msg in st.session_state.history:
    with st.chat_message(role):
        st.markdown(msg)

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.history.append(("user", prompt))

    e = parse(prompt)
    rows, val, unit, meta = retrieve(e)
    ans = llm_answer(prompt, rows, meta) or templated_answer(prompt, e, rows, val, unit, meta)

    with st.chat_message("assistant"):
        st.markdown(ans)
        if not rows.empty and meta.get("scope") in ("rank", "subnational"):
            idx = "country" if "country" in rows.columns else "region"
            st.bar_chart(rows.set_index(idx)["value"])
        elif not rows.empty and meta.get("scope") == "trend":
            st.line_chart(rows.set_index("year")["value"])
        with st.expander("Show data + how it was answered"):
            st.write(f"Parsed: `{e}`")
            st.dataframe(rows, use_container_width=True)
    st.session_state.history.append(("assistant", ans))

st.caption(f"Source: {SOURCE}  ·  Demo of the Nexus-MCP capstone. Add LLM_API_KEY (free Groq) for full natural-language answers.")
