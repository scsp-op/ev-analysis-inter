"""Filter-UI layer: the presentation-only config surface for the sidebar
filter panel, kept separate from pipeline.py's dataset-adaptation constants
and validate.py's schema-expectation constants. Renders widgets against the
just-uploaded `master` and reduces it to `filtered` -- once, upstream of
every chart in charts.py.

Filters are batched behind an explicit "Apply filters" button (st.sidebar.form):
every widget change inside the form is invisible to Python until submitted, so
picking several filters costs one rerun instead of one rerun per click. The
last-applied selection persists in st.session_state['applied_filters'] and is
what's returned on every call, regardless of what triggered the current rerun
(an unrelated button elsewhere on the page must not reset filters)."""
import streamlit as st

import pipeline

MISSING_LABEL = '(missing)'
APPLIED_STATE_KEY = 'applied_filters'

# ---- config: which descriptor columns become sidebar filters, grouped -----
# Every one of these is a plain multiselect: even columns that look numeric
# (Battery kWh, ED Wh/kg Cell) hold multi-value / free-text strings in the
# source pivot cache (e.g. '70 / 85 / 100'), so they're filtered as
# categories, not ranges.

GROUPS = {
    'Geography': ['Sales Region', 'Sales Sub-Region', 'Trade bloc', 'Sales Country'],
    'Company & brand': ['OEM Group', 'Brand', 'Parent Company', 'Parent Company Country'],
    'Vehicle & segment': ['Global Segment', 'LCV Details', 'Architecture', 'Fast-Charging'],
}
ADVANCED_GROUPS = {
    'Production': ['Vehicle Production Region', 'Vehicle Production Country'],
    'Battery & cell': ['Battery kWh', 'ED Wh/kg Cell', 'Cathode Chemistry',
                       'Cathode Mix', 'Cell Type', 'Cell Supplier', 'Cathode Supplier'],
}
FILTER_COLUMNS = ['Propulsion'] + [c for cols in GROUPS.values() for c in cols] \
    + [c for cols in ADVANCED_GROUPS.values() for c in cols]

CHART_REGION_OPTIONS = ['China', 'United States', 'Europe', 'Asia-Pacific',
                         'Americas', 'Middle East', 'Africa', 'Other']

DEFAULT_PROPULSION = ['BEV']

# Top-N cutoffs, tunable from the sidebar: only the 3 chart functions that
# actually rank/truncate by volume (as opposed to a fixed keep-list) have one.
TOP_N_DEFAULTS = {'a2_china_brands': 10, 'b1_countries': 8, 'b2_brands': 10}
TOP_N_BOUNDS = {'a2_china_brands': (3, 20), 'b1_countries': (3, 20), 'b2_brands': (3, 20)}
TOP_N_LABELS = {
    'a2_china_brands': "China's Top N brands (Broader Tracking)",
    'b1_countries': 'Top N countries in domestic-sales-by-country chart',
    'b2_brands': 'Top N brands in regional brand-ranking chart',
}

# Every widget key `render_filter_sidebar` creates -- used by the Reset button
# to clear widget state before the next rerun instantiates each fresh from
# its own `default=`.
ALL_WIDGET_KEYS = (
    ['filter_Propulsion', 'filter_chart_region', 'filter_month_range']
    + [f'filter_{c}' for c in FILTER_COLUMNS if c != 'Propulsion']
    + [f'topn_{k}' for k in TOP_N_DEFAULTS]
)


def _options(df, col):
    """Sorted display options for a multiselect: unique non-null values as
    strings, plus a synthetic '(missing)' entry if the column has any nulls
    -- lets a filter explicitly include/exclude blank rows instead of the
    blank rows being silently unreachable."""
    s = df[col]
    vals = sorted(s.dropna().astype(str).str.strip().unique().tolist())
    if s.isna().any():
        vals.append(MISSING_LABEL)
    return vals


@st.cache_data(show_spinner=False)
def _all_options(master):
    """Every filter widget's option list in one cached pass, keyed on the
    (cached, content-hashed) `master` frame -- a filter-only rerun does one
    DataFrame hash + dict lookup instead of ~20 independent column scans."""
    return {col: _options(master, col) for col in FILTER_COLUMNS if col in master.columns}


def _column_mask(df, col, selected):
    """Boolean mask for `col` matching any of `selected` (empty = no
    restriction, matches everything including nulls)."""
    if not selected:
        return None
    plain = [v for v in selected if v != MISSING_LABEL]
    mask = df[col].isna() if MISSING_LABEL in selected else (df[col] != df[col])  # all-False
    if plain:
        mask = mask | df[col].astype(str).str.strip().isin(plain)
    return mask


def default_state(master):
    """The filter state a fresh upload (or a Reset click) starts from --
    Propulsion defaults to BEV-only (matching the pre-filter dashboard),
    every other selection is unrestricted, full month range, default top-N."""
    prop_options = _options(master, 'Propulsion')
    default_prop = [p for p in DEFAULT_PROPULSION if p in prop_options] or prop_options
    months = pipeline.monthly_columns(master)
    return {
        'categorical': {col: [] if col != 'Propulsion' else default_prop for col in FILTER_COLUMNS
                        if col in master.columns},
        'chart_region': [],
        'month_range': (months[0], months[-1]) if months else None,
        'top_n': dict(TOP_N_DEFAULTS),
    }


def render_filter_sidebar(master):
    """Build the sidebar filter form against `master` (the full, unfiltered
    table) and return the last-APPLIED selection -- not necessarily what the
    widgets currently show, since nothing takes effect until 'Apply filters'
    is submitted. Widget option lists / bounds are always computed from
    `master`, never from an already-filtered frame, so they don't shrink or
    jump around as other filters change."""
    if APPLIED_STATE_KEY not in st.session_state:
        st.session_state[APPLIED_STATE_KEY] = default_state(master)

    st.sidebar.subheader('Filters')

    if st.sidebar.button('Reset all filters', key='reset_filters_btn'):
        for k in ALL_WIDGET_KEYS:
            st.session_state.pop(k, None)
        st.session_state[APPLIED_STATE_KEY] = default_state(master)
        st.rerun()

    st.sidebar.caption("Adjust filters below, then click **Apply filters** to update the charts.")

    options = _all_options(master)
    applied = st.session_state[APPLIED_STATE_KEY]

    with st.sidebar.form('filters_form'):
        pending = {'categorical': {}, 'chart_region': [], 'month_range': None, 'top_n': {}}

        pending['categorical']['Propulsion'] = st.multiselect(
            'Propulsion', options.get('Propulsion', []),
            default=applied['categorical'].get('Propulsion', []), key='filter_Propulsion',
            help='Matches today\'s dashboard default of BEV-only. Clear to include all.',
        )

        pending['chart_region'] = st.multiselect(
            'Region (chart bucket)', CHART_REGION_OPTIONS,
            default=applied.get('chart_region', []), key='filter_chart_region',
            help='The same 7-way region grouping the charts use (derived from Sales '
                 'Region / Sub-Region / Country), not the raw Sales Region column.',
        )

        months = pipeline.monthly_columns(master)
        if months:
            default_range = applied.get('month_range') or (months[0], months[-1])
            start, end = st.select_slider(
                'Month range', options=months, value=default_range, key='filter_month_range',
            )
            pending['month_range'] = (start, end)

        for group_name, cols in GROUPS.items():
            with st.expander(group_name, expanded=False):
                for col in cols:
                    if col not in master.columns:
                        continue
                    pending['categorical'][col] = st.multiselect(
                        col, options.get(col, []), default=applied['categorical'].get(col, []),
                        key=f'filter_{col}',
                    )

        with st.expander('Advanced', expanded=False):
            for group_name, cols in ADVANCED_GROUPS.items():
                st.caption(group_name)
                for col in cols:
                    if col not in master.columns:
                        continue
                    pending['categorical'][col] = st.multiselect(
                        col, options.get(col, []), default=applied['categorical'].get(col, []),
                        key=f'filter_{col}',
                    )

        with st.expander('Chart display (top N)', expanded=False):
            applied_top_n = applied.get('top_n', TOP_N_DEFAULTS)
            for topn_key, label in TOP_N_LABELS.items():
                lo, hi = TOP_N_BOUNDS[topn_key]
                pending['top_n'][topn_key] = st.slider(
                    label, min_value=lo, max_value=hi,
                    value=applied_top_n.get(topn_key, TOP_N_DEFAULTS[topn_key]),
                    key=f'topn_{topn_key}',
                )

        submitted = st.form_submit_button('Apply filters')

    if submitted:
        st.session_state[APPLIED_STATE_KEY] = pending

    return st.session_state[APPLIED_STATE_KEY]


def apply_filters(master, state):
    """Reduce `master` to the rows/columns matching `state`. Row filters are
    ANDed across every active selection; the month range drops out-of-range
    monthly columns entirely (so every pipeline.monthly_columns/years_available
    call downstream in charts.py automatically respects it) while always
    keeping the 2010/2011/2012 yearly columns, which predate monthly data."""
    df = master

    for col, selected in state['categorical'].items():
        mask = _column_mask(df, col, selected)
        if mask is not None:
            df = df[mask]

    if state['chart_region']:
        region = pipeline.chart_region(df)
        df = df[region.isin(state['chart_region'])]

    if state['month_range']:
        start, end = state['month_range']
        all_months = pipeline.monthly_columns(master)
        i0, i1 = all_months.index(start), all_months.index(end)
        keep = set(all_months[i0:i1 + 1])
        drop_cols = [c for c in all_months if c not in keep and c in df.columns]
        if drop_cols:
            df = df.drop(columns=drop_cols)

    return df
