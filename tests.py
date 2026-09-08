"""Regression tests. Run before trusting a new month:  python tests.py data/
Asserts structural invariants, determinism, and that historical months stay
consistent across releases (the strongest silent-drift tripwire)."""
import sys, glob, os
import pipeline as P
import validate as V

HIST_TOLERANCE_PCT = 3.0   # overlapping months drifted <1% in practice


def month_totals(df):
    return {m: float(df[m].fillna(0).sum()) for m in P.monthly_columns(df)}


def run(data_dir):
    files = sorted(glob.glob(os.path.join(data_dir, '*.xlsx')))
    assert files, f'no .xlsx files in {data_dir}'
    fails = []
    parsed = []
    for f in files:
        raw = P.read_raw(f)
        # determinism: identical on re-read
        assert month_totals(raw) == month_totals(P.read_raw(f)), f'non-deterministic: {f}'
        ok, rows = V.health_check(raw, f)
        for r in rows:
            if r['level'] == 'FAIL':
                fails.append(f"{os.path.basename(f)}: {r['check']} -> {r['detail']}")
        parsed.append((f, raw, month_totals(raw)))
        print(f'parsed {os.path.basename(f)}: {len(raw):,} rows, latest {P.latest_month(raw)}')

    # historical consistency across consecutive releases
    for (f1, _, t1), (f2, _, t2) in zip(parsed, parsed[1:]):
        for m in set(t1) & set(t2):
            hi = max(t1[m], t2[m])
            if hi and abs(t1[m] - t2[m]) / hi * 100 > HIST_TOLERANCE_PCT:
                fails.append(f'{m}: {os.path.basename(f1)} vs {os.path.basename(f2)} '
                             f'drift {abs(t1[m]-t2[m])/hi*100:.1f}% > {HIST_TOLERANCE_PCT}%')

    print()
    if fails:
        print('FAILED:')
        for x in fails:
            print('  -', x)
        sys.exit(1)
    print(f'ALL CHECKS PASSED across {len(files)} file(s).')


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else 'data')
