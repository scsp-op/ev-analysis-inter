"""EV sales cleaning pipeline: reads the monthly xlsx, reconstructs the pivot
source table ('raw'), builds 'master' with parent-company columns, and reports
brands that still need a parent-company mapping."""
import zipfile, io, re
import xml.etree.ElementTree as ET
import pandas as pd

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

# Segments the source pivot hides from the drill-through 'raw' (after strip)
HIDDEN_SEGMENTS = {'LSV', 'QC'}
# Parent-company-country overrides applied after the lookup
COUNTRY_OVERRIDES = {'Nissan': 'Japan', 'Mitsubishi': 'Japan'}


def _find_months_pivot(z):
    """Return (pivotTable_path, cache_definition_path, cache_records_path) for
    the Months sheet's pivot table."""
    # Months sheet -> its pivotTable -> cacheId -> cache definition/records
    wb = z.read('xl/workbook.xml').decode('utf8')
    sheets = re.findall(r'<sheet name="([^"]*)"[^>]*r:id="(rId\d+)"', wb)
    rels = z.read('xl/_rels/workbook.xml.rels').decode('utf8')
    relmap = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]*)"', rels))
    months_target = next(relmap[r] for n, r in sheets if n == 'Months').split('/')[-1]
    srels = z.read(f'xl/worksheets/_rels/{months_target}.rels').decode('utf8')
    pt_num = re.search(r'pivotTable(\d+)\.xml', srels).group(1)
    pt_path = f'xl/pivotTables/pivotTable{pt_num}.xml'
    pt = z.read(pt_path).decode('utf8')
    cache_id = re.search(r'cacheId="(\d+)"', pt).group(1)
    # map cacheId -> cache definition rId via workbook pivotCaches
    pc = re.search(rf'<pivotCache cacheId="{cache_id}" r:id="(rId\d+)"', wb).group(1)
    cdef = relmap[pc].split('/')[-1]
    cdef_path = f'xl/pivotCache/{cdef}'
    # records path from cache def rels
    crels = z.read(f'xl/pivotCache/_rels/{cdef}.rels').decode('utf8')
    rec = re.search(r'Target="([^"]*Records[^"]*)"', crels).group(1).split('/')[-1]
    return pt_path, cdef_path, f'xl/pivotCache/{rec}'


def _find_months_cache(z):
    """Return (definition_path, records_path) for the cache behind the Months pivot."""
    _, cdef_path, rec_path = _find_months_pivot(z)
    return cdef_path, rec_path


def _read_field_defs(xml_bytes, include_calculated=False):
    """Return names/shared-items for cache fields. By default, BASE fields only
    (records skip calculated fields, i.e. those with databaseField='0' like
    quarterly/CY/Cumulative); pass include_calculated=True to get every field
    in cache order, which is what lines up positionally with a pivotTable's
    own <pivotFields> list."""
    root = ET.fromstring(xml_bytes)
    names, shared = [], []
    for cf in root.iter(NS + 'cacheField'):
        if not include_calculated and cf.get('databaseField') == '0':
            continue  # calculated field, not present in records
        names.append(cf.get('name'))
        items = []
        si = cf.find(NS + 'sharedItems')
        if si is not None:
            for it in si:
                t = it.tag.split('}')[-1]
                items.append(None if t == 'm' else it.get('v'))
        shared.append(items)
    return names, shared


def source_hidden_segments(xlsx_path):
    """The 'Global Segment' values the source file's own Months pivot filters
    out (its <pivotField> item filter), independent of HIDDEN_SEGMENTS. Used by
    validate.py to catch drift between a new month's pivot filter and our
    stable, hard-coded filter -- so a filter change upstream is a visible WARN,
    never a silent behavior change here."""
    with zipfile.ZipFile(xlsx_path) as z:
        pt_path, cdef_path, _ = _find_months_pivot(z)
        pt_root = ET.fromstring(z.read(pt_path))
        names, shared = _read_field_defs(z.read(cdef_path), include_calculated=True)

    if 'Global Segment' not in names:
        return set()
    idx = names.index('Global Segment')

    pivot_fields = list(pt_root.iter(NS + 'pivotField'))
    if idx >= len(pivot_fields):
        return set()
    items = pivot_fields[idx].find(NS + 'items')
    if items is None:
        return set()

    hidden = set()
    for it in items:
        if it.tag != NS + 'item' or it.get('h') != '1':
            continue
        x = it.get('x')
        if x is None:
            continue
        x = int(x)
        if x < len(shared[idx]) and shared[idx][x] is not None:
            hidden.add(str(shared[idx][x]).strip())
    return hidden


def _read_records(xml_bytes, shared):
    rows = []
    root = ET.fromstring(xml_bytes)
    for r in root.iter(NS + 'r'):
        row = []
        for i, c in enumerate(r):
            t = c.tag.split('}')[-1]
            if t == 'x':
                row.append(shared[i][int(c.get('v'))])
            elif t == 'm':
                row.append(None)
            else:
                row.append(c.get('v'))
        rows.append(row)
    return rows


def read_raw(xlsx_path):
    """Reconstruct the drill-through 'raw' table straight from the pivot cache."""
    with zipfile.ZipFile(xlsx_path) as z:
        cdef_path, rec_path = _find_months_cache(z)
        names, shared = _read_field_defs(z.read(cdef_path))
        rows = _read_records(z.read(rec_path), shared)
    df = pd.DataFrame(rows, columns=names)
    # numeric coercion for value columns (everything after the 21 descriptors)
    for col in df.columns[21:]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    # replicate the pivot's Global Segment filter
    seg = df['Global Segment'].astype(str).str.strip()
    df = df[~seg.isin(HIDDEN_SEGMENTS)].copy()
    df['Global Segment'] = seg
    return df.reset_index(drop=True)


def build_master(raw, mapping_df):
    """raw + Parent Company / Parent Company Country columns inserted after Brand."""
    m = mapping_df.copy()
    m.columns = [c.strip() for c in m.columns]
    brand2parent = dict(zip(m['Brand'].astype(str).str.strip(),
                            m['Parent Company'].astype(str).str.strip()))
    parent2country = dict(zip(m['Parent Company'].astype(str).str.strip(),
                             m['Parent Company Country'].astype(str).str.strip()))
    master = raw.copy()
    brand = master['Brand'].astype(str).str.strip()
    parent = brand.map(brand2parent)
    country = parent.map(parent2country)
    # Nissan / Mitsubishi override
    for b, c in COUNTRY_OVERRIDES.items():
        country = country.mask(brand == b, c)
    h = master.columns.get_loc('Brand')
    master.insert(h + 1, 'Parent Company', parent)
    master.insert(h + 2, 'Parent Company Country', country)
    unmapped = sorted(brand[parent.isna()].unique().tolist())
    return master, unmapped


# ---- helpers ---------------------------------------------------------------

_MONTH = re.compile(r'^[a-z]{3}-\d{2}$')  # e.g. 'may-26'
_ORDER = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
     'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}


def monthly_columns(df):
    """Ordered list of monthly value columns, oldest -> newest. Robust to new
    months being added each release; never hard-code a month name."""
    cols = [c for c in df.columns if _MONTH.match(str(c).strip().lower())]
    def key(c):
        m, y = str(c).strip().lower().split('-')
        return (int(y), _ORDER.get(m, 99))
    return sorted(cols, key=key)


def latest_month(df):
    m = monthly_columns(df)
    return m[-1] if m else None


def load_mapping(path):
    """Read the persistent Brand -> Parent -> Country mapping. Creates an empty
    one with the right headers if the file does not exist yet."""
    import os
    cols = ['Brand', 'Parent Company', 'Parent Company Country']
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return df[cols]


def append_mapping(path, new_rows):
    """new_rows: list of dicts with Brand / Parent Company / Parent Company
    Country. Appends and de-dupes on Brand (last wins)."""
    cur = load_mapping(path)
    add = pd.DataFrame(new_rows)
    out = pd.concat([cur, add], ignore_index=True)
    out = out.drop_duplicates(subset='Brand', keep='last').sort_values('Brand')
    out.to_csv(path, index=False)
    return out


def run(xlsx_path, mapping_path, out_xlsx=None):
    """Full clean: returns (master_df, unmapped_brands). Optionally writes xlsx."""
    raw = read_raw(xlsx_path)
    master, unmapped = build_master(raw, load_mapping(mapping_path))
    if out_xlsx:
        master.to_excel(out_xlsx, index=False)
    return master, unmapped


# ---- aggregation & region helpers (for charts) -----------------------------

_QMONTHS = {1: ['jan', 'feb', 'mar'], 2: ['apr', 'may', 'jun'],
            3: ['jul', 'aug', 'sep'], 4: ['oct', 'nov', 'dec']}


def years_available(df):
    """All calendar years covered, oldest -> newest (incl. 2010-2012 yearly cols)."""
    ys = set()
    for c in df.columns:
        cs = str(c).strip()
        if re.fullmatch(r'\d{4}', cs):
            ys.add(int(cs))
        elif _MONTH.match(cs.lower()):
            ys.add(2000 + int(cs.lower().split('-')[1]))
    return sorted(ys)


def annual_total(df, year, mask=None):
    """Total units for a calendar year. Uses the yearly column when present
    (2010-2012), otherwise sums that year's monthly columns."""
    sub = df if mask is None else df[mask]
    if str(year) in df.columns:
        return float(sub[str(year)].fillna(0).sum())
    suf = f'-{year % 100:02d}'
    cols = [c for c in monthly_columns(df) if str(c).strip().lower().endswith(suf)]
    return float(sub[cols].fillna(0).sum().sum()) if cols else 0.0


def quarter_cols(df, year, q):
    suf = f'-{year % 100:02d}'
    return [f'{m}{suf}' for m in _QMONTHS[q]
            if f'{m}{suf}' in {str(c).strip().lower() for c in df.columns}]


def last_n_months(df, n=12):
    """The most recent n monthly columns (oldest->newest)."""
    return monthly_columns(df)[-n:]


def chart_region(df):
    """Canonical 7-way region per row, matching the internal dashboard:
    China / United States are pulled out as their own buckets, so 'Asia-Pacific'
    excludes China and 'Americas' excludes the US automatically. Middle East and
    Africa are split via Sub-Region."""
    country = df['Sales Country'].astype(str).str.strip()
    sub = df['Sales Sub-Region'].astype(str).str.strip()
    reg = df['Sales Region'].astype(str).str.strip()
    out = pd.Series('Other', index=df.index)
    out = out.mask(reg == 'Americas', 'Americas')
    out = out.mask(reg == 'Asia-Pacific', 'Asia-Pacific')
    out = out.mask(reg.isin(['Europe (Western & Central)', 'Eastern Europe']), 'Europe')
    out = out.mask(sub == 'Africa', 'Africa')
    out = out.mask(sub == 'Middle East', 'Middle East')
    out = out.mask(country == 'USA', 'United States')
    out = out.mask((country == 'China') | (sub == 'China'), 'China')
    return out
