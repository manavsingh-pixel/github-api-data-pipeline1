"""Streamlit dashboard for GitHub repository snapshots."""
import os
import sqlite3
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "github_pipeline.db")))
st.set_page_config(page_title="GitHub Repository Insights", page_icon="📊", layout="wide")
st.title("GitHub Repository Insights")
st.caption("A snapshot-based view of public repositories returned by GitHub Search API. Metrics describe the selected sample, not all GitHub repositories.")

if not DB_PATH.exists():
    st.warning("No database found yet. Run the pipeline first: `python -m src.pipeline` from the project folder.")
    st.stop()

@st.cache_data(ttl=60)
def load_data(db_path: str):
    with sqlite3.connect(db_path) as conn:
        repos = pd.read_sql_query("SELECT * FROM repositories", conn)
        summaries = pd.read_sql_query("SELECT * FROM language_summary", conn)
        raw = pd.read_sql_query("SELECT * FROM raw_api_responses", conn)
    return repos, summaries, raw

repos, summaries, raw = load_data(str(DB_PATH))
if repos.empty:
    st.info("The database has no repository snapshots. Run the pipeline first.")
    st.stop()

snapshots = sorted(repos["snapshot_date"].dropna().unique().tolist())
selected_day = st.sidebar.selectbox("Snapshot date", snapshots, index=len(snapshots)-1)
data = repos[repos["snapshot_date"] == selected_day].copy()
lang_options = sorted(data["language"].fillna("Unknown").unique().tolist())
selected_languages = st.sidebar.multiselect("Languages", lang_options, default=lang_options)
filtered = data[data["language"].isin(selected_languages)]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Repositories in view", f"{len(filtered):,}")
c2.metric("Stars in view", f"{int(filtered['stars'].sum()):,}")
c3.metric("Open issues in view", f"{int(filtered['open_issues'].sum()):,}")
c4.metric("Languages in view", f"{filtered['language'].nunique():,}")

left, right = st.columns(2)
with left:
    by_lang = filtered.groupby("language", as_index=False).agg(repository_count=("repo_id", "count"), total_stars=("stars", "sum")).sort_values("repository_count", ascending=False).head(15)
    st.subheader("Repositories by language")
    if not by_lang.empty:
        st.plotly_chart(px.bar(by_lang.sort_values("repository_count"), x="repository_count", y="language", orientation="h", labels={"repository_count":"Repositories", "language":"Primary language"}), use_container_width=True)
with right:
    st.subheader("Star concentration by language")
    if not by_lang.empty:
        stars_lang = by_lang.sort_values("total_stars", ascending=False).head(12)
        st.plotly_chart(px.bar(stars_lang, x="language", y="total_stars", labels={"language":"Primary language", "total_stars":"Total stars"}), use_container_width=True)

st.subheader("Snapshot trend")
trend = repos.groupby("snapshot_date", as_index=False).agg(repository_count=("repo_id", "count"), total_stars=("stars", "sum"), total_open_issues=("open_issues", "sum"))
if len(trend) < 2:
    st.info("Only one snapshot is available. Run the pipeline on later days to build a time trend; the current snapshot is not historical data.")
else:
    st.plotly_chart(px.line(trend, x="snapshot_date", y=["repository_count", "total_stars"], markers=True, labels={"value":"Value", "snapshot_date":"Snapshot date", "variable":"Metric"}), use_container_width=True)

st.subheader("Top repositories in the selected view")
top = filtered.sort_values(["stars", "full_name"], ascending=[False, True]).head(25)
show_cols = ["full_name", "language", "stars", "forks", "open_issues", "updated_at", "html_url"]
st.dataframe(top[show_cols], use_container_width=True, hide_index=True, column_config={"html_url": st.column_config.LinkColumn("Repository link")})
with st.expander("Pipeline run diagnostics"):
    st.write(f"Raw API page records stored: **{len(raw):,}**")
    if not raw.empty:
        st.dataframe(raw[["fetched_at", "page", "status_code", "error_message"]].sort_values("fetched_at", ascending=False), use_container_width=True, hide_index=True)
    st.caption("Raw responses are retained for audit/debugging. A failed API page is logged; the pipeline will not replace the clean snapshot if it retrieved no valid records.")
