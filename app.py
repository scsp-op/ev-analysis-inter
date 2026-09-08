"""Single-page Streamlit app for cleaning a monthly EV sales export and
producing the standard chart set. Runs entirely locally / in-memory."""
import html
import io
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import charts
import filters
import pipeline
import validate

MAPPING_PATH = "parent_company_mapping.csv"
CONSISTENCY_DATA_DIR = "data"
_LEVEL_ICON = {"PASS": "✅", "WARN": "⚠️", "FAIL": "\U0001f6d1"}


@st.cache_data(show_spinner=False)
def _cached_build_master(raw, mapping_df):
    return pipeline.build_master(raw, mapping_df)


@st.cache_data(show_spinner=False)
def _cached_health_check(master, xlsx_path, mapping_df):
    return validate.health_check(master, xlsx_path, mapping_df)

st.set_page_config(page_title="EV Sales Cleaner", layout="wide")
st.title("EV Sales Cleaner")

st.markdown(
    """
    <style>
    .health-summary {
        display:flex; align-items:center; gap:.5rem;
        font-weight:600; font-size:1.05rem;
        padding:.6rem 1rem; border-radius:.5rem; margin-bottom:.75rem;
    }
    .health-summary.ok   { background: rgba(16,185,129,.14); color:#0f9d6c; }
    .health-summary.warn { background: rgba(245,158,11,.16); color:#b45309; }
    .health-summary.fail { background: rgba(239,68,68,.16);  color:#b91c1c; }
    .health-row {
        display:flex; gap:.55rem; align-items:flex-start;
        padding:.5rem .75rem; border-radius:.4rem; margin-bottom:.4rem;
        border-left:3px solid transparent; font-size:.88rem; line-height:1.45;
    }
    .health-row.warn { background: rgba(245,158,11,.10); border-left-color:#f59e0b; }
    .health-row.fail { background: rgba(239,68,68,.10);  border-left-color:#ef4444; }
    .health-row .name { font-weight:600; }
    .health-row .detail { opacity:.85; }
    .health-pass-line { font-size:.85rem; opacity:.75; padding:.15rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_health_check(rows):
    """Compact status panel: one summary badge, WARN/FAIL always shown in
    full (never buried), PASS rows collapsed since there's nothing to act on."""
    st.subheader("Data health check")

    fails = [r for r in rows if r["level"] == "FAIL"]
    warns = [r for r in rows if r["level"] == "WARN"]
    passes = [r for r in rows if r["level"] == "PASS"]

    if fails:
        cls, icon, label = "fail", _LEVEL_ICON["FAIL"], f"{len(fails)} check(s) failed"
    elif warns:
        cls, icon, label = "warn", _LEVEL_ICON["WARN"], f"{len(warns)} warning(s) -- not blocking"
    else:
        cls, icon, label = "ok", _LEVEL_ICON["PASS"], "All checks passed"
    st.markdown(f'<div class="health-summary {cls}">{icon} {label}</div>', unsafe_allow_html=True)

    for r in fails + warns:
        cls = r["level"].lower()
        name, detail = html.escape(r["check"]), html.escape(r["detail"])
        st.markdown(
            f'<div class="health-row {cls}">{_LEVEL_ICON[r["level"]]}'
            f'<span><span class="name">{name}</span> -- <span class="detail">{detail}</span></span></div>',
            unsafe_allow_html=True,
        )

    if passes:
        with st.expander(f"{len(passes)} check(s) passed", expanded=False):
            for r in passes:
                name, detail = html.escape(r["check"]), html.escape(r["detail"])
                st.markdown(
                    f'<div class="health-pass-line">{_LEVEL_ICON["PASS"]} '
                    f'<b>{name}</b> -- {detail}</div>',
                    unsafe_allow_html=True,
                )


with st.sidebar:
    st.subheader("Historical consistency check")
    st.caption(
        "Different from the health check on the page: this replays **every** "
        f"file in `{CONSISTENCY_DATA_DIR}/` (not just the one uploaded above) "
        "and checks they still agree with each other -- each one parses "
        "cleanly, and overlapping months haven't drifted more than 3% between "
        "releases. It's a check on your local archive, so it doesn't affect "
        "what's rendered on this page. Run it after dropping a new monthly "
        f"export into `{CONSISTENCY_DATA_DIR}/`."
    )
    if st.button("Run consistency check"):
        with st.spinner("Running tests.py..."):
            result = subprocess.run(
                [sys.executable, "tests.py", CONSISTENCY_DATA_DIR],
                cwd=Path(__file__).parent, capture_output=True, text=True,
            )
        (st.success if result.returncode == 0 else st.error)(
            "ALL CHECKS PASSED" if result.returncode == 0 else "Consistency check FAILED"
        )
        st.code((result.stdout + result.stderr).strip() or "(no output)")

if "raw" not in st.session_state:
    st.session_state.raw = None
    st.session_state.raw_file_name = None
    st.session_state.xlsx_path = None
    st.session_state.read_error = None

uploaded = st.file_uploader("Upload the month's .xlsx export", type=["xlsx"])

if uploaded is not None and uploaded.name != st.session_state.raw_file_name:
    with st.spinner("Reading raw pivot data..."):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name
        old_path = st.session_state.xlsx_path
        try:
            new_raw = pipeline.read_raw(tmp_path)
        except Exception as e:
            # Structurally unreadable pivot cache -- abnormal, not a data
            # change. Record it as a FAIL rather than letting Streamlit show
            # a raw traceback.
            st.session_state.raw = None
            st.session_state.read_error = str(e)
            Path(tmp_path).unlink(missing_ok=True)
        else:
            st.session_state.raw = new_raw
            st.session_state.read_error = None
            st.session_state.xlsx_path = tmp_path
            if old_path:
                Path(old_path).unlink(missing_ok=True)
        st.session_state.raw_file_name = uploaded.name

raw = st.session_state.raw
xlsx_path = st.session_state.xlsx_path

if st.session_state.read_error:
    render_health_check([
        {"level": "FAIL", "check": "pivot cache unreadable", "detail": st.session_state.read_error},
    ])
    st.error(
        "This file's structure doesn't match what this pipeline expects, so "
        "nothing here can be trusted. Charts and the master download are "
        "disabled until this is resolved."
    )
    st.stop()

if raw is None:
    st.info("Upload a monthly .xlsx file to begin.")
    st.stop()

mapping_df = pipeline.load_mapping(MAPPING_PATH)
master, unmapped = _cached_build_master(raw, mapping_df)

ok, health_rows = _cached_health_check(master, xlsx_path, mapping_df)
render_health_check(health_rows)

if not ok:
    st.error(
        "A required check failed -- the file structure looks different from what "
        "this pipeline expects, so the numbers below can't be trusted. Charts and "
        "the master download are disabled until this is resolved."
    )
    st.stop()

latest = pipeline.latest_month(master)
total_units = master[latest].fillna(0).sum() if latest else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Rows", f"{len(master):,}")
col2.metric("Latest month", latest or "n/a")
col3.metric(f"Total units ({latest})" if latest else "Total units", f"{total_units:,.0f}")
col4.metric("Unmapped brands", len(unmapped))

if unmapped:
    st.subheader("Unmapped brands")
    st.warning(
        "These brands have no parent-company mapping yet. Any left blank below "
        "will keep blank Parent Company / Parent Company Country in the master table."
    )
    edit_df = pd.DataFrame({
        "Brand": unmapped,
        "Parent Company": ["" for _ in unmapped],
        "Parent Company Country": ["" for _ in unmapped],
    })
    edited = st.data_editor(
        edit_df, num_rows="fixed", key="unmapped_editor", use_container_width=True
    )

    if st.button("Save mappings"):
        filled = edited[edited["Parent Company"].astype(str).str.strip() != ""]
        rows = filled.to_dict("records")
        if rows:
            pipeline.append_mapping(MAPPING_PATH, rows)
            st.success(f"Saved {len(rows)} mapping(s).")
            st.rerun()
        else:
            st.info("No Parent Company values were filled in -- nothing saved.")

buf = io.BytesIO()
master.to_excel(buf, index=False, engine="openpyxl")
st.download_button(
    "Download master.xlsx",
    data=buf.getvalue(),
    file_name="master.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

def render_chart(title, fig, key):
    st.markdown(f"#### {title}")
    st.plotly_chart(fig, use_container_width=True, theme=None, key=f"plot_{key}")
    png_buf = io.BytesIO()
    fig.write_image(png_buf, format="png", scale=2)
    st.download_button(
        f"Download PNG -- {title}",
        data=png_buf.getvalue(),
        file_name=f"{title.lower().replace(' ', '_')}.png",
        mime="image/png",
        key=key,
    )


filter_state = filters.render_filter_sidebar(master)
filtered = filters.apply_filters(master, filter_state)

st.subheader("Charts")
st.caption(f"{len(filtered):,} of {len(master):,} rows match the current filters.")

with st.expander("Broader Tracking", expanded=True):
    for title, fn in charts.BROADER:
        render_chart(title, fn(filtered), key=f"broader_{title}")

with st.expander("Region-Specific Tracking", expanded=False):
    for region_name, mask_fn in charts.REGIONS.items():
        st.markdown(f"### {region_name}")
        mask = mask_fn(filtered)
        for title, fig in charts.region_charts(filtered, region_name, mask):
            render_chart(title, fig, key=f"region_{region_name}_{title}")
