"""Filter-UI layer: the presentation-only config surface for the sidebar
filter panel, kept separate from pipeline.py's dataset-adaptation constants
and validate.py's schema-expectation constants. Renders widgets against the
just-uploaded `master` and reduces it to `filtered` -- once, upstream of
every chart in charts.py."""
import streamlit as st

import pipeline

MISSING_LABEL = '(missing)'

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

CHART_REGION_OPTIONS = ['China', 'United States', 'Europe', 'Asia-Pacific',
                         'Americas', 'Middle East', 'Africa', 'Other']

DEFAULT_PROPULSION = ['BEV']


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


def render_filter_sidebar(master):
    """Build every filter widget from `master` (the full, unfiltered table)
    and return the resulting selections as a plain dict -- `apply_filters`
    turns this into the actual boolean mask. Widget option lists / bounds are
    always computed from `master`, never from an already-filtered frame, so
    they don't shrink or jump around as other filters change."""
    state = {'categorical': {}, 'chart_region': [], 'month_range': None}

    st.sidebar.subheader('Filters')

    prop_options = _options(master, 'Propulsion')
    default_prop = [p for p in DEFAULT_PROPULSION if p in prop_options] or prop_options
    state['categorical']['Propulsion'] = st.sidebar.multiselect(
        'Propulsion', prop_options, default=default_prop, key='filter_Propulsion',
        help='Matches today\'s dashboard default of BEV-only. Clear to include all.',
    )

    state['chart_region'] = st.sidebar.multiselect(
        'Region (chart bucket)', CHART_REGION_OPTIONS, default=[], key='filter_chart_region',
        help='The same 7-way region grouping the charts use (derived from Sales '
             'Region / Sub-Region / Country), not the raw Sales Region column.',
    )

    months = pipeline.monthly_columns(master)
    if months:
        start, end = st.sidebar.select_slider(
            'Month range', options=months, value=(months[0], months[-1]), key='filter_month_range',
        )
        state['month_range'] = (start, end)

    for group_name, cols in GROUPS.items():
        with st.sidebar.expander(group_name, expanded=False):
            for col in cols:
                if col not in master.columns:
                    continue
                state['categorical'][col] = st.multiselect(
                    col, _options(master, col), default=[], key=f'filter_{col}',
                )

    with st.sidebar.expander('Advanced', expanded=False):
        for group_name, cols in ADVANCED_GROUPS.items():
            st.caption(group_name)
            for col in cols:
                if col not in master.columns:
                    continue
                state['categorical'][col] = st.multiselect(
                    col, _options(master, col), default=[], key=f'filter_{col}',
                )

    return state


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
