"""
Forest Climate Analyzer - natural-language demo (countries + regions + visuals).

Ask in plain English about countries OR subnational regions; get a grounded
natural-language answer AND a relevant chart. Mirrors the MCP server: rule-based
router extracts intent + entities, runs parameterized SQL over the warehouse,
and (with a free Groq key) an LLM phrases the answer from ONLY the retrieved rows.
"""
import os
import re
import json
import sqlite3
import pandas as pd
import streamlit as st

DB_PATH = os.getenv("DEMO_DB", os.path.join(os.path.dirname(__file__), "data", "forest_demo.db"))
SOURCE = "Hansen/UMD/Google/USGS/NASA - Global Forest Watch; GFW forest-carbon flux."

VAL = {"tree_cover": "tree_cover_loss_ha", "carbon": "carbon_emissions_mg_co2e", "primary": "primary_forest_loss_ha"}
UNIT = {"tree_cover": "ha", "carbon": "Mg CO₂e", "primary": "ha"}
LABEL = {"tree_cover": "tree cover loss", "carbon": "forest-carbon emissions", "primary": "primary-forest loss"}
TBL_C = {"tree_cover": "fact_tree_cover_loss", "carbon": "fact_carbon", "primary": "fact_primary_forest"}
TBL_S = {"tree_cover": "fact_subnational_tree_cover_loss", "carbon": "fact_subnational_carbon", "primary": "fact_subnational_primary_forest"}

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


@st.cache_data
def region_map():
    """name_lower -> (country, region). Collisions resolved by largest total loss."""
    rdf = q("SELECT country, subnational1, SUM(tree_cover_loss_ha) t "
            "FROM fact_subnational_tree_cover_loss GROUP BY country, subnational1 ORDER BY t DESC")
    m = {}
    for _, r in rdf.iterrows():
        nm = str(r["subnational1"]).strip().lower()
        if len(nm) >= 4 and nm != "?" and nm not in m:
            m[nm] = (r["country"], r["subnational1"])
    return m


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
    for alias, canon in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(alias) + r"\b", t):
            return canon
    for c in sorted(country_list(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(c.lower()) + r"\b", t):
            return c
    return None


def detect_region(text):
    t = text.lower()
    rm = region_map()
    for nm in sorted(rm, key=len, reverse=True):
        if re.search(r"\b" + re.escape(nm) + r"\b", t):
            return rm[nm]
    return None


def detect_year(text):
    yrs = [int(x) for x in re.findall(r"20[0-2]\d", text) if 2001 <= int(x) <= 2024]
    return yrs[-1] if yrs else None


def parse(text):
    t = text.lower()
    thr_m = re.search(r"\b(\d{1,2})\s*%", text)
    thr = int(thr_m.group(1)) if thr_m and int(thr_m.group(1)) in (0, 10, 15, 20, 25, 30, 50, 75) else 30
    e = {"country": detect_country(text), "region": None, "year": detect_year(text), "threshold": thr}

    if re.search(r"\b(carbon|co2|emission|emissions|greenhouse)\b", t):
        e["metric"] = "carbon"
    elif re.search(r"\b(primary|virgin|old[\s-]?growth|pristine)\b", t):
        e["metric"] = "primary"
    else:
        e["metric"] = "tree_cover"

    if re.search(r"\b(state|states|province|provinces|region|regions|subnational)\b", t):
        base = "subnational"
    elif re.search(r"\b(top|most|highest|worst|rank|leading|biggest)\b", t):
        base = "rank"
    elif re.search(r"\b(trend|over time|since|history|historical|each year|by year)\b", t):
        base = "trend"
    else:
        base = "single"

    reg = detect_region(text)
    if reg and (e["country"] is None or e["country"] == reg[0]):
        e["country"], e["region"] = reg[0], reg[1]
        if base == "trend":
            e["scope"] = "region_trend"
        elif base in ("rank", "subnational"):
            e["scope"] = base
        else:
            e["scope"] = "region"
    else:
        e["scope"] = base
    return e


def fnum(v):
    try:
        return f"{float(v):,.0f}"
    except Exception:
        return str(v)


def get_trend(country, metric, thr):
    has_thr = metric != "primary"
    where = "country=?" + (" AND threshold=?" if has_thr else "")
    params = (country, thr) if has_thr else (country,)
    return q(f"SELECT year, {VAL[metric]} AS value FROM {TBL_C[metric]} WHERE {where} ORDER BY year", params)


def get_region_trend(country, region, metric):
    has_thr = metric != "primary"
    where = "country=? AND subnational1=?" + (" AND threshold=30" if has_thr else "")
    return q(f"SELECT year, {VAL[metric]} AS value FROM {TBL_S[metric]} WHERE {where} ORDER BY year", (country, region))


def retrieve(e):
    metric, scope = e["metric"], e["scope"]
    thr = 30 if metric != "tree_cover" else e["threshold"]
    has_thr = metric != "primary"

    if scope == "region":
        y = e["year"] or 2023
        where = "country=? AND subnational1=? AND year=?" + (" AND threshold=30" if has_thr else "")
        rows = q(f"SELECT {VAL[metric]} AS value FROM {TBL_S[metric]} WHERE {where}", (e["country"], e["region"], y))
        return rows, {"scope": "region", "year": y}

    if scope == "region_trend":
        return get_region_trend(e["country"], e["region"], metric), {"scope": "region_trend"}

    if scope == "subnational" and e["country"]:
        y = e["year"] or 2023
        where = "country=? AND year=?" + (" AND threshold=30" if has_thr else "")
        rows = q(f"SELECT subnational1 AS region, {VAL[metric]} AS value FROM {TBL_S[metric]} "
                 f"WHERE {where} ORDER BY {VAL[metric]} DESC LIMIT 10", (e["country"], y))
        return rows, {"scope": "subnational", "year": y}

    if scope == "rank":
        y1, y2 = (e["year"], e["year"]) if e["year"] else (2020, 2024)
        where = "year BETWEEN ? AND ?" + (" AND threshold=?" if has_thr else "")
        params = (y1, y2, thr) if has_thr else (y1, y2)
        rows = q(f"SELECT country, SUM({VAL[metric]}) AS value FROM {TBL_C[metric]} "
                 f"WHERE {where} GROUP BY country ORDER BY value DESC LIMIT 10", params)
        return rows, {"scope": "rank", "y1": y1, "y2": y2}

    if scope == "trend" and e["country"]:
        return get_trend(e["country"], metric, thr), {"scope": "trend"}

    if e["country"]:
        y = e["year"] or 2023
        where = "country=? AND year=?" + (" AND threshold=?" if has_thr else "")
        params = (e["country"], y, thr) if has_thr else (e["country"], y)
        rows = q(f"SELECT {VAL[metric]} AS value FROM {TBL_C[metric]} WHERE {where}", params)
        return rows, {"scope": "single", "year": y, "threshold": thr}
    return pd.DataFrame(), {"scope": "none"}


def templated_answer(e, rows, meta):
    if rows.empty:
        if not e["country"] and meta.get("scope") != "rank":
            return "I couldn't spot a country or region in your question. Try *\"forest loss in Brazil in 2023\"*, *\"Djelfa forest loss\"*, or *\"top carbon-emitting countries\"*."
        return "No data for that combination. Try another year (2001–2024)."
    m, unit = LABEL[e["metric"]], UNIT[e["metric"]]
    s = meta["scope"]
    if s == "region":
        extra = " (30% canopy)" if e["metric"] == "tree_cover" else ""
        return f"**{e['region']} ({e['country']})** had **{fnum(rows['value'].iloc[0])} {unit}** of {m} in **{meta['year']}**{extra}."
    if s == "region_trend":
        tot = rows["value"].sum(); peak = rows.loc[rows["value"].idxmax()]
        return f"**{e['region']} ({e['country']})** {m} totaled **{fnum(tot)} {unit}** over {len(rows)} years; worst year was **{int(peak['year'])}** ({fnum(peak['value'])} {unit})."
    if s == "single":
        extra = f" (at {meta['threshold']}% canopy)" if e["metric"] == "tree_cover" else ""
        return f"**{e['country']}** had **{fnum(rows['value'].iloc[0])} {unit}** of {m} in **{meta['year']}**{extra}."
    if s == "rank":
        lines = [f"{i+1}. {r['country']} — {fnum(r['value'])} {unit}" for i, r in rows.reset_index(drop=True).iterrows()]
        return f"**Top countries by {m} ({meta['y1']}–{meta['y2']}):**\n\n" + "\n".join(lines)
    if s == "subnational":
        lines = [f"{i+1}. {r['region']} — {fnum(r['value'])} {unit}" for i, r in rows.reset_index(drop=True).iterrows()]
        return f"**Top regions in {e['country']} by {m} ({meta['year']}):**\n\n" + "\n".join(lines)
    if s == "trend":
        tot = rows["value"].sum(); peak = rows.loc[rows["value"].idxmax()]
        return f"**{e['country']}** {m} totaled **{fnum(tot)} {unit}** over {len(rows)} years; worst year was **{int(peak['year'])}** ({fnum(peak['value'])} {unit})."
    return "Here's what I found."


def build_context(e, rows, meta):
    """Gather grounded supporting facts so the LLM can explain, not just state."""
    s = meta.get("scope")
    metric = e["metric"]
    ctx = {"metric": LABEL[metric], "unit": UNIT[metric], "scope": s}
    if rows is None or rows.empty:
        return ctx
    has_thr = metric != "primary"
    thr = 30 if metric != "tree_cover" else e["threshold"]
    if s in ("single", "region"):
        ctx["place"] = e["region"] if s == "region" else e["country"]
        ctx["country"] = e["country"]
        ctx["year"] = meta.get("year")
        ctx["value"] = round(float(rows["value"].iloc[0]), 1)
        tr = (get_region_trend(e["country"], e["region"], metric) if s == "region"
              else get_trend(e["country"], metric, thr))
        ctx["yearly"] = [{"year": int(r["year"]), "value": round(float(r["value"]), 1)}
                         for _, r in tr.iterrows()]
        try:
            if s == "single":
                w = "year=? " + ("AND threshold=? " if has_thr else "") + f"AND {VAL[metric]} > ?"
                p = (ctx["year"], thr, ctx["value"]) if has_thr else (ctx["year"], ctx["value"])
                higher = q(f"SELECT COUNT(*) c FROM {TBL_C[metric]} WHERE {w}", p)["c"].iloc[0]
                total = q(f"SELECT COUNT(DISTINCT country) c FROM {TBL_C[metric]} WHERE year=?", (ctx["year"],))["c"].iloc[0]
                ctx["rank_among_countries"] = int(higher) + 1
                ctx["total_countries"] = int(total)
            else:
                w = "country=? AND year=? " + ("AND threshold=30 " if has_thr else "") + f"AND {VAL[metric]} > ?"
                higher = q(f"SELECT COUNT(*) c FROM {TBL_S[metric]} WHERE {w}", (e["country"], ctx["year"], ctx["value"]))["c"].iloc[0]
                total = q(f"SELECT COUNT(DISTINCT subnational1) c FROM {TBL_S[metric]} WHERE country=? AND year=?", (e["country"], ctx["year"]))["c"].iloc[0]
                ctx["rank_among_regions_in_country"] = int(higher) + 1
                ctx["total_regions_in_country"] = int(total)
        except Exception:
            pass
    elif s in ("rank", "subnational"):
        ctx["place"] = e.get("country")
        ctx["ranking"] = [{k: (round(float(v), 1) if isinstance(v, (int, float)) else v)
                           for k, v in r.items()} for r in rows.head(10).to_dict("records")]
        ctx.update({k: v for k, v in meta.items() if k != "scope"})
    elif s in ("trend", "region_trend"):
        ctx["place"] = e["region"] if s == "region_trend" else e["country"]
        ctx["yearly"] = [{"year": int(r["year"]), "value": round(float(r["value"]), 1)}
                         for _, r in rows.iterrows()]
    return ctx


def llm_answer(text, context):
    key = os.getenv("LLM_API_KEY") or (st.secrets.get("LLM_API_KEY", "") if hasattr(st, "secrets") else "")
    if not key or not context:
        return None
    if context.get("value") is None and "ranking" not in context and "yearly" not in context:
        return None
    import httpx
    sys = ("You are a forest and climate data analyst. Answer the user's question in 2-4 sentences. "
           "First give the headline figure, then add one or two insights grounded ONLY in the provided data - "
           "for example how the value ranks among countries or regions, the overall trend direction, how it "
           "compares to other years, or notable peak years. Never invent any number that is not in the data. "
           "Format numbers with commas and write clearly for a non-expert. Do not mention JSON or 'the data'.")
    usr = f"Question: {text}\n\nGrounded data (JSON):\n{json.dumps(context)}\n\nWrite the explanatory answer."
    try:
        r = httpx.post(os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"),
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json={"model": os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
                             "messages": [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                             "max_tokens": 500, "temperature": 0.5}, timeout=30.0)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    return None


def render_visual(e, rows, meta):
    s = meta.get("scope")
    if rows.empty:
        return
    unit = UNIT[e["metric"]]
    if s in ("single", "region"):
        place = e["region"] if s == "region" else e["country"]
        c1, c2 = st.columns([1, 2])
        c1.metric(f"{place} · {meta['year']}", f"{fnum(rows['value'].iloc[0])} {unit}", help=LABEL[e["metric"]])
        tr = (get_region_trend(e["country"], e["region"], e["metric"]) if s == "region"
              else get_trend(e["country"], e["metric"], 30 if e["metric"] != "tree_cover" else e["threshold"]))
        if not tr.empty:
            c2.caption(f"{place} — {LABEL[e['metric']]} over time ({unit})")
            c2.line_chart(tr.set_index("year")["value"])
    elif s == "rank":
        st.bar_chart(rows.set_index("country")["value"])
    elif s == "subnational":
        st.bar_chart(rows.set_index("region")["value"])
    elif s in ("trend", "region_trend"):
        st.line_chart(rows.set_index("year")["value"])


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
st.title("🌳 Forest Climate Analyzer")
st.caption("Ask in plain English about 24 years of Global Forest Watch data — "
           "165 countries, 2,779 regions. Answers come with charts.")

examples = [
    "How much forest did Brazil lose in 2023?",
    "Top 10 countries for tree cover loss since 2020",
    "Which states in Indonesia emit the most carbon?",
    "Djelfa forest loss trend",
]
st.write("**Try:**")
cols = st.columns(2)
clicked = None
for i, ex in enumerate(examples):
    if cols[i % 2].button(ex, use_container_width=True):
        clicked = ex

if "history" not in st.session_state:
    st.session_state.history = []

for role, msg in st.session_state.history:
    with st.chat_message(role):
        st.markdown(msg)

prompt = st.chat_input("Ask about a country or region...") or clicked

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.history.append(("user", prompt))

    e = parse(prompt)
    rows, meta = retrieve(e)
    ctx = build_context(e, rows, meta)
    ans = llm_answer(prompt, ctx) or templated_answer(e, rows, meta)

    with st.chat_message("assistant"):
        st.markdown(ans)
        render_visual(e, rows, meta)
        with st.expander("How it was answered (parsed query + data)"):
            st.write(f"Interpreted as: `{e}`")
            st.dataframe(rows, use_container_width=True)
    st.session_state.history.append(("assistant", ans))

st.caption(f"Source: {SOURCE}  ·  Demo of the Nexus-MCP capstone. Add LLM_API_KEY (free Groq) for full natural-language answers.")
