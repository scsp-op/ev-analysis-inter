"""Internal BEV dashboard: 8 'Broader Tracking' charts + 4 region-specific charts
(Southeast Asia, Europe). Every chart is BEV-only and reads through pipeline.py's
tested helpers -- nothing here reimplements read_raw/build_master/monthly math.

All chart-specific magic strings (palette, country/region lists) live in the
config block below so re-theming or re-scoping is a one-edit change.

Charts are Plotly figures (go.Figure / go.Figure via make_subplots), rendered
by app.py through st.plotly_chart -- responsive vector/HTML, not raster
images, so they stay crisp at any container width and get hover/zoom/legend-
toggle for free.
"""
import math

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

import pipeline

# Importing `streamlit` (via app.py) repoints pio.templates.default to its own
# "streamlit" template, whose colors/fonts are unset placeholders (e.g.
# '#000001') meant to be swapped client-side -- but only when st.plotly_chart
# is called with theme="streamlit". app.py renders with theme=None, so those
# placeholders would otherwise be drawn literally, making legends/labels
# unreadable. Reassert a real (light) default so every figure here gets sane
# colors -- every chart also forces a white plot/paper background below, so
# the template must be the light variant, not 'plotly_dark' (whose default
# font color is light and would be invisible on that forced-white background).
pio.templates.default = 'plotly_white'

# ---- config: palette -------------------------------------------------------

COLORS = {'blue': '#0A3161', 'dark_red': '#851432', 'light_red': '#B31942',
          'black': '#000000', 'dark_grey': '#7B7B7B', 'light_grey': '#F7F7F7',
          'white': '#FFFFFF'}

# Lightened blue for "US/Tesla global" series -- owner did not specify a hex
# for this, picked as a tint of COLORS['blue']. EDIT freely.
LIGHT_BLUE = '#7C93B8'

# Parent Company Country -> fill. Storage key for the US is 'USA' (matches the
# mapping CSV / build_master output); 'United States' is kept as an alias so
# display code can look either up.
COUNTRY_COLORS = {
    'China': '#851432', 'USA': '#0A3161', 'United States': '#0A3161',
    'Germany': '#8297d1', 'South Korea': '#aed2f5', 'Vietnam': '#662a73',
    'Japan': '#1758af', 'Malaysia': '#436829', 'Indonesia': '#da7448',
    'France': '#7B7B7B',  # France not owner-specified -- EDIT
    'Other': '#7B7B7B',
}

REGION_COLORS = {  # EDIT freely -- approximations of the doc
    'China': '#851432', 'United States': '#0A3161', 'Europe': '#8297d1',
    'Asia-Pacific': '#B31942', 'Americas': '#7B7B7B', 'Middle East': '#662a73',
    'Africa': '#aed2f5', 'Other': '#F7F7F7',
}

# China domestic vs foreign = dark_red vs light_red (used throughout).
# Tesla US vs global = blue vs LIGHT_BLUE (used throughout).

# ---- config: editable constants -------------------------------------------

SEA_COUNTRIES = ['Thailand', 'Vietnam', 'Indonesia', 'Malaysia', 'Singapore',
                  'Philippines', 'Laos', 'Cambodia']
EUROPE_SUBREGIONS = ['Western Europe', 'Central Europe']
EUROPE_PARENT_COUNTRIES = ['Germany', 'France', 'Sweden', 'United Kingdom', 'Italy',
                            'Spain', 'Netherlands', 'Czech Republic']  # EDIT to match your mapping
CHINESE_OWNED_FOREIGN_BRANDS = ['Volvo', 'Polestar', 'Lotus', 'LEVC', 'Proton', 'Smart',
                                 'MG', 'MG Motor', 'LDV', 'Maxus']  # from doc note -- EDIT

DISPLAY_COUNTRY = {'USA': 'United States'}


# ---- small shared helpers ---------------------------------------------------

_PROPULSION_ORDER = ['BEV', 'PHEV', 'FCEV']


def _propulsion_label(df):
    """Label reflecting whichever Propulsion value(s) actually survived the
    sidebar filter (default is BEV-only, matching the old hard-coded
    behavior, but a chart must not keep saying 'BEV' once a user filters to
    PHEV or FCEV)."""
    present = set(df['Propulsion'].astype(str).str.strip().unique())
    vals = [v for v in _PROPULSION_ORDER if v in present]
    return '/'.join(vals) if vals else 'EV'


def _disp(country):
    return DISPLAY_COUNTRY.get(country, country)


def _country_color(country):
    return COUNTRY_COLORS.get(country, COLORS['dark_grey'])


def _region_color(region):
    return REGION_COLORS.get(region, COLORS['light_grey'])


def _years(df, start=None, end=None):
    ys = pipeline.years_available(df)
    if start is not None:
        ys = [y for y in ys if y >= start]
    if end is not None:
        ys = [y for y in ys if y <= end]
    return ys


def _annual_series(df, years, mask=None):
    return [pipeline.annual_total(df, y, mask=mask) for y in years]


def _bucket(series, keep):
    """Map a categorical Series to one of `keep`, everything else -> 'Other'."""
    s = series.fillna('Other')
    return s.where(s.isin(keep), 'Other')


def _fmt(v):
    return f'{v:,.0f}'


def _rank_brands(df, months=12, top=10):
    cols = pipeline.last_n_months(df, months)
    totals = df.groupby('Brand')[cols].sum().fillna(0).sum(axis=1)
    return totals.sort_values(ascending=False).head(top)


# ---- shared Plotly figure factory ------------------------------------------

DEFAULT_HEIGHT = 440


def _new_fig(height=DEFAULT_HEIGHT, width=None, margin=None, rows=1, cols=1, **subplot_kwargs):
    fig = go.Figure() if (rows, cols) == (1, 1) else make_subplots(rows=rows, cols=cols, **subplot_kwargs)
    fig.update_layout(
        height=height,
        width=width,  # None => on-screen sizing is fully delegated to
                      # use_container_width=True; only consulted by
                      # fig.write_image() for the static PNG export path.
        margin=margin or dict(l=60, r=30, t=60, b=50),
        font=dict(size=12, color=COLORS['black']),
        legend=dict(font=dict(size=10, color=COLORS['black'])),
        plot_bgcolor=COLORS['white'],
        paper_bgcolor=COLORS['white'],
    )
    return fig


def _caption(fig, text, y, size=9, color=None, yanchor=None):
    """fig.text(...) equivalent -- a paper-relative footnote/caption below (or
    above) the plotting area, extending into the figure margin."""
    fig.add_annotation(
        text=text, xref='paper', yref='paper', x=0.5, y=y, showarrow=False,
        xanchor='center', yanchor=yanchor or ('bottom' if y >= 1 else 'top'),
        font=dict(size=size, color=color or COLORS['dark_grey']),
    )


def _empty_fig(message='No data for the current filters'):
    """Placeholder figure for a chart whose working frame has 0 rows or 0
    usable years/months once filters are applied -- keeps every chart
    function returning a valid go.Figure (fig.write_image needs one) instead
    of crashing on an empty groupby/index lookup."""
    fig = _new_fig(height=200)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.add_annotation(text=message, xref='paper', yref='paper', x=0.5, y=0.5,
                        showarrow=False, font=dict(size=14, color=COLORS['dark_grey']))
    return fig


def _bar_label(fig, x, y, text, orientation='v', size=8):
    """Per-bar text label, replacing an ax.text(...) call anchored to a bar."""
    if orientation == 'v':
        fig.add_annotation(x=x, y=y, text=text, showarrow=False, yanchor='bottom', font=dict(size=size))
    else:
        fig.add_annotation(x=x, y=y, text=text, showarrow=False, xanchor='left', yanchor='middle', font=dict(size=size))


# =================  SECTION A -- BROADER TRACKING  =========================

def a1_prc_vs_us_domestic(master):
    bev = master
    years = _years(bev, start=2010)
    if bev.empty or not years:
        return _empty_fig()
    label = _propulsion_label(bev)
    china_mask = bev['Sales Country'].astype(str).str.strip() == 'China'
    usa_mask = bev['Sales Country'].astype(str).str.strip() == 'USA'
    china = _annual_series(bev, years, mask=china_mask)
    usa = _annual_series(bev, years, mask=usa_mask)

    latest_year = years[-1]
    suffix = f'-{latest_year % 100:02d}'
    months_in_latest_year = [m for m in pipeline.monthly_columns(bev) if m.endswith(suffix)]
    partial = len(months_in_latest_year) < 12

    china_plot = [v if v > 0 else float('nan') for v in china]
    usa_plot = [v if v > 0 else float('nan') for v in usa]

    fig = _new_fig(height=480, margin=dict(t=90, b=60))

    def _add_series(y_vals, color, name):
        hover = '%{x}: %{y:,.0f}<extra>' + name + '</extra>'
        if partial and len(years) >= 2:
            fig.add_trace(go.Scatter(x=years[:-1], y=y_vals[:-1], mode='lines+markers', name=name,
                                      line=dict(color=color), marker=dict(color=color), hovertemplate=hover))
            fig.add_trace(go.Scatter(x=years[-2:], y=y_vals[-2:], mode='lines+markers', name=name,
                                      line=dict(color=color, dash='dash'), marker=dict(color=color),
                                      showlegend=False, hovertemplate=hover))
        else:
            fig.add_trace(go.Scatter(x=years, y=y_vals, mode='lines+markers', name=name,
                                      line=dict(color=color), marker=dict(color=color), hovertemplate=hover))

    _add_series(china_plot, COLORS['dark_red'], 'China')
    _add_series(usa_plot, COLORS['blue'], 'United States')

    fig.update_yaxes(type='log', title=f'{label} units sold domestically (log scale)')
    fig.update_xaxes(title='Year')
    fig.add_vline(x=2015, line_dash='dot', line_color=COLORS['dark_grey'], line_width=0.8)
    fig.add_annotation(x=2015, xref='x', y=0.98, yref='paper', text='Made in China 2025',
                        textangle=90, showarrow=False, xanchor='right', yanchor='top',
                        font=dict(size=7, color=COLORS['dark_grey']))
    fig.add_vrect(x0=2021, x1=min(2025, years[-1]), fillcolor=COLORS['light_grey'],
                  opacity=0.5, line_width=0, layer='below')
    fig.add_annotation(x=2023, xref='x', y=0.02, yref='paper', text='14th Five-Year Plan',
                        showarrow=False, xanchor='center', yanchor='bottom',
                        font=dict(size=7, color=COLORS['dark_grey']))
    fig.update_layout(title=dict(
        text=f'PRC vs U.S. Domestic {label} Market',
        subtitle=dict(text=f'{label} sales in China have outpaced the US since 2015.',
                       font=dict(size=9, color=COLORS['dark_grey'])),
    ))
    return fig


def a2_china_top10_brands(master):
    bev = master
    china = bev[bev['Parent Company Country'] == 'China']
    if china.empty:
        return _empty_fig()
    label = _propulsion_label(bev)
    cols = pipeline.last_n_months(china, 12)
    totals = china.groupby('Brand')[cols].sum().fillna(0).sum(axis=1).sort_values(ascending=False)
    top10 = totals.head(10)

    domestic_mask = china['Sales Country'].astype(str).str.strip() == 'China'
    domestic = china[domestic_mask].groupby('Brand')[cols].sum().fillna(0).sum(axis=1) \
        .reindex(top10.index).fillna(0)
    intl = (top10 - domestic).clip(lower=0)

    brands = list(top10.index)[::-1]  # reverse so largest renders on top
    dom_vals = [domestic[b] for b in brands]
    intl_vals = [intl[b] for b in brands]
    bar_totals = [d + i for d, i in zip(dom_vals, intl_vals)]

    fig = _new_fig(height=440)
    fig.add_trace(go.Bar(orientation='h', x=dom_vals, y=brands, marker_color=COLORS['dark_red'],
                          name='Domestic', hovertemplate='%{y}: %{x:,.0f}<extra>Domestic</extra>'))
    fig.add_trace(go.Bar(orientation='h', x=intl_vals, y=brands, marker_color=COLORS['light_red'],
                          name='International', hovertemplate='%{y}: %{x:,.0f}<extra>International</extra>'))
    fig.update_layout(barmode='stack')

    for b, d, i, total in zip(brands, dom_vals, intl_vals, bar_totals):
        pct_intl = (i / total * 100) if total else 0
        _bar_label(fig, x=total, y=b, text=f'  {_fmt(total)} ({pct_intl:.0f}% intl.)', orientation='h')

    max_total = max(bar_totals) if bar_totals else 1
    fig.update_xaxes(title=f'{label} units', range=[0, max_total * 1.3])
    fig.update_layout(title=f"China's Top 10 {label} Brands (last 12 months)",
                       legend=dict(x=0.98, y=0.02, xanchor='right', yanchor='bottom'))
    return fig


def a3_foreign_chinese_sales_by_region(master):
    bev = master
    china_foreign = bev[(bev['Parent Company Country'] == 'China') &
                         (bev['Sales Country'].astype(str).str.strip() != 'China')]
    all_months = pipeline.monthly_columns(bev)
    if china_foreign.empty or not all_months:
        return _empty_fig()
    label = _propulsion_label(bev)
    start_idx = all_months.index('jan-20') if 'jan-20' in all_months else 0
    cols = all_months[start_idx:]

    region = pipeline.chart_region(china_foreign)
    grouped = china_foreign.groupby(region)[cols].sum().fillna(0)

    fig = _new_fig(height=460, margin=dict(b=90))
    for reg in ['Europe', 'Asia-Pacific', 'Americas', 'Middle East', 'Africa']:
        vals = grouped.loc[reg] if reg in grouped.index else pd.Series(0.0, index=cols)
        fig.add_trace(go.Scatter(x=cols, y=vals.values, mode='lines', name=reg,
                                  line=dict(color=_region_color(reg)),
                                  hovertemplate='%{x}: %{y:,.0f}<extra>' + reg + '</extra>'))

    n_ticks = min(24, len(cols))
    tick_idx = [round(i) for i in np.linspace(0, len(cols) - 1, num=n_ticks)] if n_ticks else []
    fig.update_xaxes(type='category', tickmode='array',
                      tickvals=[cols[i] for i in tick_idx], ticktext=[cols[i] for i in tick_idx],
                      tickangle=90, tickfont=dict(size=6), title='Month')
    fig.update_yaxes(title=f'{label} units')
    fig.update_layout(title=f'Foreign Sales of Chinese {label}s by Region')
    return fig


def a4_chinese_foreign_sales_by_company(master):
    bev = master
    china_foreign = bev[(bev['Parent Company Country'] == 'China') &
                         (bev['Sales Country'].astype(str).str.strip() != 'China')]
    years = _years(china_foreign, start=2020)
    if china_foreign.empty or not years:
        return _empty_fig()
    label = _propulsion_label(bev)

    companies = china_foreign['Parent Company'].dropna().unique().tolist()
    lifetime = {c: sum(_annual_series(china_foreign, years, mask=(china_foreign['Parent Company'] == c)))
                for c in companies}
    top3 = sorted(lifetime, key=lifetime.get, reverse=True)[:3]

    fig = _new_fig(height=440, margin=dict(t=70))
    palette = [COLORS['dark_red'], COLORS['light_red'], COLORS['blue']]
    yearly_totals = _annual_series(china_foreign, years)

    for i, company in enumerate(top3):
        vals = _annual_series(china_foreign, years, mask=(china_foreign['Parent Company'] == company))
        fig.add_trace(go.Bar(x=years, y=vals, name=company, marker_color=palette[i],
                              hovertemplate='%{x}: %{y:,.0f}<extra>' + company + '</extra>'))

    other_mask = ~china_foreign['Parent Company'].isin(top3)
    other_vals = _annual_series(china_foreign, years, mask=other_mask)
    fig.add_trace(go.Bar(x=years, y=other_vals, name='Other',
                          marker=dict(color=COLORS['light_grey'], line=dict(color=COLORS['dark_grey'], width=1)),
                          hovertemplate='%{x}: %{y:,.0f}<extra>Other</extra>'))
    fig.update_layout(barmode='stack')

    for x, total, other in zip(years, yearly_totals, other_vals):
        top3_share = ((total - other) / total * 100) if total else 0
        _bar_label(fig, x=x, y=total, text=f'{top3_share:.0f}%')

    fig.update_xaxes(title='Year')
    fig.update_yaxes(title=f'{label} units (foreign sales)')
    fig.update_layout(title=f'Chinese {label} Sales Outside of China by Company')
    return fig


def a5_global_bev_by_region(master):
    bev = master
    years = _years(bev, start=2015)
    if bev.empty or not years:
        return _empty_fig()
    label = _propulsion_label(bev)
    panels = [
        ('PRC', bev['Parent Company Country'] == 'China'),
        ('United States', bev['Parent Company Country'] == 'USA'),
        ('Europe', bev['Parent Company Country'].isin(EUROPE_PARENT_COUNTRIES)),
    ]
    all_regions = ['China', 'United States', 'Europe', 'Asia-Pacific', 'Americas', 'Middle East', 'Africa', 'Other']

    fig = _new_fig(height=560, margin=dict(t=110, b=170), rows=1, cols=3,
                    shared_yaxes=True, subplot_titles=[name for name, _ in panels])
    fig.update_layout(barmode='stack')

    seen = set()
    for panel_idx, (name, mask) in enumerate(panels):
        panel_df = bev[mask]
        region = pipeline.chart_region(panel_df)
        totals = _annual_series(panel_df, years)
        for reg in all_regions:
            reg_mask = region == reg
            if not reg_mask.any():
                continue
            vals = _annual_series(panel_df, years, mask=reg_mask)
            pct = [v / t * 100 if t else 0 for v, t in zip(vals, totals)]
            show = reg not in seen
            seen.add(reg)
            fig.add_trace(
                go.Bar(x=years, y=pct, name=reg, marker_color=_region_color(reg),
                       legendgroup=reg, showlegend=show,
                       hovertemplate='%{x}: %{y:.0f}%<extra>' + reg + '</extra>'),
                row=1, col=panel_idx + 1,
            )
        fig.update_xaxes(title_text='Year', row=1, col=panel_idx + 1)

    fig.update_yaxes(title_text=f'% of group {label} sales', row=1, col=1)
    fig.update_layout(
        legend=dict(orientation='h', yanchor='top', y=-0.22, xanchor='center', x=0.5, font=dict(size=8)),
        title=dict(text=f'Global {label} Sales by Region', x=0.5),
    )
    _caption(fig, 'Americas excludes the US market; Asia-Pacific excludes China.', y=-0.34, size=7)
    return fig


def a6_global_bev_by_parent_country(master):
    bev = master
    years = _years(bev, start=2015)
    if bev.empty or not years:
        return _empty_fig()
    label = _propulsion_label(bev)
    keep = ['China', 'USA', 'Germany', 'South Korea', 'France']
    bucketed = _bucket(bev['Parent Company Country'], keep)

    fig = _new_fig(height=460, margin=dict(t=90, b=60))
    totals = _annual_series(bev, years)
    china_vals = [0.0] * len(years)
    for country in keep + ['Other']:
        c_mask = bucketed == country
        vals = _annual_series(bev, years, mask=c_mask)
        fig.add_trace(go.Bar(x=years, y=vals, name=_disp(country), marker_color=_country_color(country),
                              hovertemplate='%{x}: %{y:,.0f}<extra>' + _disp(country) + '</extra>'))
        if country == 'China':
            china_vals = vals
    fig.update_layout(barmode='stack')

    for x, cv, t in zip(years, china_vals, totals):
        share = (cv / t * 100) if t else 0
        _bar_label(fig, x=x, y=t, text=f'{share:.0f}%')

    latest_share = (china_vals[-1] / totals[-1] * 100) if totals[-1] else 0
    fig.update_xaxes(title='Year')
    fig.update_yaxes(title=f'{label} units')
    fig.update_layout(title=dict(
        text=f'Global {label} Sales by Parent Company Country',
        subtitle=dict(text=f'China holds {latest_share:.0f}% of global {label} sales as of {years[-1]}.',
                       font=dict(size=9, color=COLORS['dark_grey'])),
    ))
    return fig


def a7_tesla_vs_byd(master):
    bev = master
    tb = bev[bev['Brand'].isin(['Tesla', 'BYD'])]
    years = _years(tb, start=2015)
    if tb.empty or not years:
        return _empty_fig()
    label = _propulsion_label(bev)

    byd_dom = _annual_series(tb, years, mask=(tb['Brand'] == 'BYD') & (tb['Sales Country'] == 'China'))
    byd_for = _annual_series(tb, years, mask=(tb['Brand'] == 'BYD') & (tb['Sales Country'] != 'China'))
    tsl_dom = _annual_series(tb, years, mask=(tb['Brand'] == 'Tesla') & (tb['Sales Country'] == 'USA'))
    tsl_for = _annual_series(tb, years, mask=(tb['Brand'] == 'Tesla') & (tb['Sales Country'] != 'USA'))

    fig = _new_fig(height=430)
    fig.add_trace(go.Bar(x=years, y=tsl_dom, name='Tesla domestic (USA)', marker_color=COLORS['blue'],
                          offsetgroup='Tesla', hovertemplate='%{x}: %{y:,.0f}<extra>Tesla domestic (USA)</extra>'))
    fig.add_trace(go.Bar(x=years, y=tsl_for, name='Tesla foreign', marker_color=LIGHT_BLUE,
                          offsetgroup='Tesla', hovertemplate='%{x}: %{y:,.0f}<extra>Tesla foreign</extra>'))
    fig.add_trace(go.Bar(x=years, y=byd_dom, name='BYD domestic (China)', marker_color=COLORS['dark_red'],
                          offsetgroup='BYD', hovertemplate='%{x}: %{y:,.0f}<extra>BYD domestic (China)</extra>'))
    fig.add_trace(go.Bar(x=years, y=byd_for, name='BYD foreign', marker_color=COLORS['light_red'],
                          offsetgroup='BYD', hovertemplate='%{x}: %{y:,.0f}<extra>BYD foreign</extra>'))
    fig.update_layout(barmode='stack')
    fig.update_xaxes(title='Year')
    fig.update_yaxes(title=f'{label} units')
    fig.update_layout(title=f'Tesla vs BYD {label} Sales')
    return fig


def a8_us_vs_prc_foreign(master):
    bev = master
    years = _years(bev, start=2010)
    if bev.empty or not years:
        return _empty_fig()
    label = _propulsion_label(bev)

    us_foreign = (bev['Parent Company Country'] == 'USA') & (bev['Sales Country'] != 'USA')
    china_foreign = (bev['Parent Company Country'] == 'China') & (bev['Sales Country'] != 'China')
    china_indigenous = china_foreign & (~bev['Brand'].isin(CHINESE_OWNED_FOREIGN_BRANDS))
    china_owned_foreign = (bev['Parent Company Country'] == 'China') & \
        bev['Brand'].isin(CHINESE_OWNED_FOREIGN_BRANDS) & (bev['Sales Country'] != 'China')

    series = {
        'United States': (us_foreign, COLORS['blue']),
        'China (all)': (china_foreign, COLORS['dark_red']),
        'China (indigenous)': (china_indigenous, COLORS['light_red']),
        'China (Chinese-owned foreign brands)': (china_owned_foreign, '#662a73'),
    }

    fig = _new_fig(height=440, margin=dict(b=80))
    for series_name, (mask, color) in series.items():
        vals = _annual_series(bev, years, mask=mask)
        fig.add_trace(go.Scatter(x=years, y=vals, mode='lines+markers', name=series_name,
                                  line=dict(color=color), marker=dict(color=color),
                                  hovertemplate='%{x}: %{y:,.0f}<extra>' + series_name + '</extra>'))

    fig.update_xaxes(title='Year')
    fig.update_yaxes(title=f'Foreign {label} units')
    fig.update_layout(title=f'US vs PRC Foreign {label} Sales', legend=dict(font=dict(size=8)))
    _caption(fig, 'Chinese-owned foreign brands: ' + ', '.join(CHINESE_OWNED_FOREIGN_BRANDS), y=-0.20, size=7)
    return fig


BROADER = [
    ('PRC vs U.S. Domestic BEV Market', a1_prc_vs_us_domestic),
    ("China's Top 10 BEV Brands", a2_china_top10_brands),
    ('Foreign Sales of Chinese EVs by Region', a3_foreign_chinese_sales_by_region),
    ('Chinese BEV Sales Outside of China by Company', a4_chinese_foreign_sales_by_company),
    ('Global BEV Sales by Region', a5_global_bev_by_region),
    ('Global BEV Sales by Parent Company Country', a6_global_bev_by_parent_country),
    ('Tesla vs BYD BEV Sales', a7_tesla_vs_byd),
    ('US vs PRC Foreign BEV Sales', a8_us_vs_prc_foreign),
]


# =================  SECTION B -- REGION-SPECIFIC  ===========================

def _b1_domestic_by_country(region_bev, region_name):
    years = _years(region_bev, start=2022)
    if region_bev.empty or not years:
        return _empty_fig(f'No data in {region_name} for the current filters')
    label = _propulsion_label(region_bev)
    quarters = []  # (label, cols)
    for y in years:
        for q in (1, 2, 3, 4):
            cols = pipeline.quarter_cols(region_bev, y, q)
            if cols:
                quarters.append((f'Q{q} {y}', cols))

    if region_name == 'Southeast Asia':
        countries = SEA_COUNTRIES
    else:
        cols_all = [c for _, c in quarters]
        flat_cols = [c for cs in cols_all for c in cs]
        totals = region_bev.groupby('Sales Country')[flat_cols].sum().fillna(0).sum(axis=1)
        countries = totals.sort_values(ascending=False).head(8).index.tolist()

    fig = _new_fig(height=460, margin=dict(b=90, r=140))
    labels = [lbl for lbl, _ in quarters]
    for country in countries:
        c_df = region_bev[region_bev['Sales Country'] == country]
        vals = [c_df[cols].sum().fillna(0).sum() for _, cols in quarters]
        fig.add_trace(go.Scatter(x=labels, y=vals, mode='lines+markers', marker=dict(size=3), name=country,
                                  hovertemplate='%{x}: %{y:,.0f}<extra>' + country + '</extra>'))

    fig.update_xaxes(type='category', tickangle=90, tickfont=dict(size=7), title='Quarter')
    fig.update_yaxes(title=f'{label} units')
    fig.update_layout(title=f'Domestic {label} Sales in {region_name} by Country', legend=dict(font=dict(size=7)))
    return fig


def _b2_top_brands(region_bev, region_name):
    # HOOK: the doc's SEA version splits into bespoke segments (Vietnamese
    # domestic/overseas, Chinese-Malaysian JVs, etc.). Not implemented here --
    # this generalized parent-company-country stack applies to every region.
    # Hand-customize this function for Southeast Asia if that breakdown is needed.
    if region_bev.empty:
        return _empty_fig(f'No data in {region_name} for the current filters')
    label = _propulsion_label(region_bev)
    top10 = _rank_brands(region_bev, months=12, top=10)
    cols = pipeline.last_n_months(region_bev, 12)
    brands = list(top10.index)
    sub = region_bev[region_bev['Brand'].isin(brands)].copy()
    sub['_country'] = _bucket(sub['Parent Company Country'], list(COUNTRY_COLORS.keys()))
    pivot = sub.groupby(['Brand', '_country'])[cols].sum().fillna(0).sum(axis=1).unstack('_country').fillna(0)
    pivot = pivot.reindex(brands)

    brands_rev = brands[::-1]
    fig = _new_fig(height=480)
    for country in pivot.columns:
        vals = pivot.loc[brands_rev, country]
        fig.add_trace(go.Bar(orientation='h', x=vals, y=brands_rev, name=_disp(country),
                              marker_color=_country_color(country),
                              hovertemplate='%{y}: %{x:,.0f}<extra>' + _disp(country) + '</extra>'))
    fig.update_layout(barmode='stack')
    fig.update_xaxes(title=f'{label} units')
    fig.update_layout(title=f"{region_name}'s Top 10 {label} Brands (last 12 months)",
                       legend=dict(x=0.98, y=0.02, xanchor='right', yanchor='bottom', font=dict(size=7)))
    return fig


def _b3_donut(region_bev, region_name):
    # HOOK: the doc splits Vietnam into domestic/international for the SEA
    # version of this chart. Not implemented -- default is a straight
    # parent-company-country donut, same as every other region.
    if region_bev.empty:
        return _empty_fig(f'No data in {region_name} for the current filters')
    label = _propulsion_label(region_bev)
    cols = pipeline.last_n_months(region_bev, 12)
    country = _bucket(region_bev['Parent Company Country'], list(COUNTRY_COLORS.keys()))
    totals = region_bev.groupby(country)[cols].sum().fillna(0).sum(axis=1)
    totals = totals[totals > 0]
    total_sum = totals.sum()

    if total_sum > 0:
        pct = totals / total_sum * 100
        small = pct[pct < 2].index.tolist()
        if small:
            other = totals.get('Other', 0.0) + totals.loc[small].sum()
            totals = totals.drop(index=[c for c in small if c != 'Other'])
            totals.loc['Other'] = other

    labels = [_disp(c) for c in totals.index]
    colors = [_country_color(c) for c in totals.index]

    # matplotlib's wedgeprops(width=0.4) means the ring occupies 40% of the
    # radius -- Plotly's `hole` is the *empty* fraction, so hole = 1 - 0.4.
    fig = _new_fig(height=480)
    fig.add_trace(go.Pie(labels=labels, values=totals.values, marker=dict(colors=colors), hole=0.6,
                          texttemplate='%{percent:.0%}', textposition='inside', textfont=dict(size=8),
                          hovertemplate='%{label}: %{value:,.0f} (%{percent})<extra></extra>'))
    fig.update_layout(title=f'{label} Sales in {region_name} by Parent Company Country (last 12 months)')
    return fig


def _b4_small_multiples(region_bev, region_name):
    years = _years(region_bev, start=2022)
    if region_bev.empty or not years:
        return _empty_fig(f'No data in {region_name} for the current filters')
    label = _propulsion_label(region_bev)
    countries = sorted(region_bev['Sales Country'].dropna().unique().tolist())
    # Cap facet count: Plotly's facet grid errors out past ~30 rows (vertical
    # spacing can't shrink enough), and an unfiltered/broadly-filtered "Custom"
    # region can easily span 100+ countries. Curated regions (SEA, Europe)
    # never hit this cap; it only bites the free-form case.
    MAX_FACETS = 24
    if len(countries) > MAX_FACETS:
        cols_all = pipeline.last_n_months(region_bev, 12)
        totals = region_bev.groupby('Sales Country')[cols_all].sum().fillna(0).sum(axis=1)
        countries = sorted(totals.sort_values(ascending=False).head(MAX_FACETS).index.tolist())
    n = max(len(countries), 1)
    ncols = min(6, n)
    nrows = math.ceil(n / ncols)

    rows = []
    for country in countries:
        c_df = region_bev[region_bev['Sales Country'] == country]
        china_total = sum(_annual_series(c_df, years, mask=(c_df['Parent Company Country'] == 'China')))
        us_total = sum(_annual_series(c_df, years, mask=(c_df['Parent Company Country'] == 'USA')))
        rows.append({'Country': country, 'Series': 'China', 'Total': china_total})
        rows.append({'Country': country, 'Series': 'US', 'Total': us_total})
    long_df = pd.DataFrame(rows)

    height = max(320, 140 * nrows + 120)
    fig = px.bar(long_df, x='Series', y='Total', facet_col='Country', facet_col_wrap=ncols,
                 color='Series', color_discrete_map={'China': COLORS['dark_red'], 'US': COLORS['blue']},
                 height=height)
    fig.update_layout(showlegend=False, title=f'PRC vs US {label} Sales in {region_name} ({years[0]}-{years[-1]})',
                       plot_bgcolor=COLORS['white'], paper_bgcolor=COLORS['white'],
                       font=dict(size=12, color=COLORS['black']))
    fig.for_each_annotation(lambda a: a.update(text=a.text.split('=')[-1], font=dict(size=8)))
    fig.update_xaxes(tickfont=dict(size=6), title=None)
    fig.update_yaxes(tickfont=dict(size=6), title=None)
    fig.update_traces(hovertemplate='%{x}: %{y:,.0f}<extra></extra>')
    return fig


REGIONS = {
    'Southeast Asia': lambda master: master['Sales Country'].isin(SEA_COUNTRIES),
    'Europe': lambda master: master['Sales Sub-Region'].astype(str).str.strip().isin(EUROPE_SUBREGIONS),
    # Lets the b1-b4 chart template render for any ad-hoc sidebar filter
    # combination, not just the two curated presets above.
    'Custom (current filter)': lambda master: pd.Series(True, index=master.index),
}


def region_charts(master, region_name, mask):
    """mask: boolean Series aligned to `master`'s index (e.g. from REGIONS[name](master))."""
    region_bev = master[mask]
    return [
        (f'Domestic BEV Sales in {region_name} by Country', _b1_domestic_by_country(region_bev, region_name)),
        (f"{region_name}'s Top 10 BEV Brands", _b2_top_brands(region_bev, region_name)),
        (f'BEV Sales in {region_name} by Parent Company Country', _b3_donut(region_bev, region_name)),
        (f'PRC vs US BEV Sales in {region_name}', _b4_small_multiples(region_bev, region_name)),
    ]
