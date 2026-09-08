"""Self-check layer for the EV pipeline. Runs on every file load and returns a
structured report so silent drift becomes a visible warning instead of a wrong
chart. Nothing here mutates data; it only inspects."""
import pipeline as P

# What the pipeline assumes about a well-formed monthly file. Update deliberately.
REQUIRED_COLUMNS = ['Sales Region', 'Sales Sub-Region', 'Sales Country', 'OEM Group',
                    'Brand', 'Global Segment', 'Propulsion']
EXPECTED_PROPULSION = {'BEV', 'PHEV', 'FCEV'}
EXPECTED_REGIONS = {'Europe (Western & Central)', 'Asia-Pacific', 'Americas',
                    'Africa & ME', 'Eastern Europe'}
CONFIGURED_SEGMENT_FILTER = set(P.HIDDEN_SEGMENTS)   # {'LSV','QC'}
ROW_BAND = (12000, 60000)          # sane row-count window
OTHER_REGION_MAX_PCT = 2.0         # chart_region 'Other' share ceiling


def _row(level, check, detail):
    return {'level': level, 'check': check, 'detail': detail}


def health_check(master, xlsx_path=None, mapping=None):
    """Returns (ok, rows). ok is False if any FAIL. rows are dicts with
    level in {PASS, WARN, FAIL}. Designed to be rendered as a table in the UI."""
    rows = []

    missing = [c for c in REQUIRED_COLUMNS if c not in master.columns]
    rows.append(_row('FAIL' if missing else 'PASS', 'required columns',
                     f'missing: {missing}' if missing else 'all present'))
    if missing:
        return False, rows  # nothing else is trustworthy

    n = len(master)
    lo, hi = ROW_BAND
    rows.append(_row('PASS' if lo <= n <= hi else 'WARN', 'row count',
                     f'{n:,} rows' + ('' if lo <= n <= hi else f' (outside {lo:,}-{hi:,})')))

    lm = P.latest_month(master)
    rows.append(_row('FAIL' if lm is None else 'PASS', 'latest month',
                     lm or 'no monthly columns detected'))

    prop = set(master['Propulsion'].astype(str).str.strip().unique())
    unexpected = prop - EXPECTED_PROPULSION
    rows.append(_row('WARN' if unexpected else 'PASS', 'propulsion values',
                     f'new categories: {sorted(unexpected)}' if unexpected else 'BEV/PHEV/FCEV only'))

    regs = set(master['Sales Region'].astype(str).str.strip().unique())
    newr = regs - EXPECTED_REGIONS
    rows.append(_row('WARN' if newr else 'PASS', 'sales regions',
                     f'unrecognized: {sorted(newr)}' if newr else 'all 5 known'))

    cr = P.chart_region(master)
    if lm:
        tot = master[lm].fillna(0).sum()
        other = master.loc[cr == 'Other', lm].fillna(0).sum()
        pct = (other / tot * 100) if tot else 0
        rows.append(_row('WARN' if pct > OTHER_REGION_MAX_PCT else 'PASS',
                         "region mapping ('Other' share)", f'{pct:.1f}% of latest-month units'))

    if xlsx_path is not None:
        try:
            src = set(P.source_hidden_segments(xlsx_path))
            # ignore whitespace-duplicate artifacts already handled by strip
            meaningful = {s for s in src if s not in {'Car-E', 'MPV-E', 'Car-D', 'MPV-D'}}
            drift = meaningful ^ CONFIGURED_SEGMENT_FILTER
            rows.append(_row('WARN' if drift else 'PASS', 'source segment filter',
                             f"file hides {sorted(src)}; pipeline drops {sorted(CONFIGURED_SEGMENT_FILTER)}"
                             + (' — DRIFT, review' if drift else '')))
        except Exception as e:
            rows.append(_row('WARN', 'source segment filter', f'could not read: {e}'))

    if mapping is not None:
        _, unmapped = P.build_master(P.read_raw(xlsx_path), mapping) if xlsx_path else (None, [])
        rows.append(_row('WARN' if unmapped else 'PASS', 'mapping coverage',
                         f'{len(unmapped)} brand(s) need parent-company research'
                         if unmapped else 'every brand mapped'))

    ok = not any(r['level'] == 'FAIL' for r in rows)
    return ok, rows
