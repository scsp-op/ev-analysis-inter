# EV Sales Cleaner

A local Streamlit app for cleaning monthly EV sales exports and generating a
fixed set of charts. Everything (upload, cleaning, charts) runs locally / in
memory -- no database, no cloud upload. The one exception is chart PNG
export: `kaleido` drives a local Chrome to rasterize the chart, and if it
can't find one already installed it fetches a headless Chrome build the
first time you click a "Download PNG" button, which needs network access.

## One-time setup

1. Install Python 3.11-3.13.
2. From this directory:
   - macOS/Linux: `./run.sh`
   - Windows: `run.bat`

   This installs the pinned dependencies from `requirements.txt` and starts
   the app at http://localhost:8501.
3. Before your first PNG download, make sure Chrome is reachable: if you
   already have Google Chrome installed, kaleido will use it automatically;
   otherwise run `kaleido_get_chrome` once (from the venv) to fetch a
   headless Chrome build. This is a one-time step per machine.
4. `parent_company_mapping.csv` ships empty (header only). You'll fill it in
   from the app as you process your first few months.

## Monthly usage

1. Run `./run.sh` (or `run.bat`).
2. Upload the month's `.xlsx` export in the browser.
3. Read the **Data health check** panel at the top of the results before
   trusting anything below it -- this checks only the file you just
   uploaded, and runs automatically on every upload:
   - ✅ all-green summary -- normal; the individual passing checks are
     tucked into a collapsed "N check(s) passed" expander since there's
     nothing to act on.
   - ⚠️ amber warning row(s) -- shown in full, never collapsed, but does not
     block anything (a new brand, a new sales region, or the source file's
     own segment filter drifting from `pipeline.HIDDEN_SEGMENTS`). Worth a
     look, safe to proceed.
   - 🛑 red failure row(s) -- the file's structure doesn't match what the
     pipeline expects (a required column is missing, the pivot cache didn't
     parse, or no monthly columns were found). The app stops here on
     purpose: charts and the master download are hidden until this is
     fixed, because the numbers can't be trusted.
4. Check the summary panel: row count, detected latest month, total units for
   that month, and how many brands are still unmapped.
5. If there are unmapped brands, fill in **Parent Company** and
   **Parent Company Country** for as many as you can in the editable table,
   then click **Save mappings**. This appends to
   `parent_company_mapping.csv` and refreshes the master table immediately.
   Any brand left blank stays blank in the master table until you map it in a
   future session. Unmapped brands never block the charts or the download --
   only a FAIL in the health check does that.
6. Click **Download master.xlsx** to save the cleaned table.
7. Review the charts and use each chart's **Download PNG** button to save the
   ones you need.
8. Separately, whenever you add a new monthly file to your local `data/`
   folder, run the **historical consistency check** (below) against that
   whole folder -- it's independent of steps 1-7 above.

## Historical consistency check

The per-upload health check above only looks at the one file in the
browser. `tests.py` is a second, separate check: the silent-drift tripwire
that replays **every** file in a folder together and asserts each one
parses, that extraction is deterministic on re-read, that the health check
reports no FAILs, and that overlapping historical months haven't moved more
than 3% between releases (the strongest signal that something upstream
changed). It has nothing to do with what's currently uploaded in the app.

Run it whenever a new monthly file is added to `data/`:

```
python tests.py data/
```

It prints `ALL CHECKS PASSED across N file(s).` on success, or a `FAILED:`
list (and a non-zero exit code) on failure. The app's sidebar has a
**Run consistency check** button that runs this same command against
`data/` and shows the result inline, so you don't need a terminal open --
it does not affect the file uploaded on the page.

## Notes

- `pipeline.py` is the tested data layer (pivot-cache extraction, master
  build, mapping helpers) and `validate.py` is the tested self-check layer
  (`health_check`) that `app.py` and `tests.py` both call -- integrate them,
  don't reimplement their logic elsewhere.
- Every dataset-specific assumption lives in exactly two places, so pointing
  this at a different sales dataset means editing only these constants, never
  scattering new hard-coded columns/months/categories through `app.py` or
  `charts.py`:
  - `pipeline.HIDDEN_SEGMENTS` / `pipeline.COUNTRY_OVERRIDES`
  - `validate.REQUIRED_COLUMNS` / `EXPECTED_PROPULSION` / `EXPECTED_REGIONS` /
    `ROW_BAND`
- `HIDDEN_SEGMENTS` is a deliberate, stable filter -- it's checked against
  each file's own pivot filter (`pipeline.source_hidden_segments`) but never
  auto-updated from it. If a month's filter should be matched exactly, that's
  a one-line edit to `HIDDEN_SEGMENTS`, made on purpose, not automatically.
- `charts.py` holds the chart set as `BROADER` (a list of `(title, function)`
  pairs) plus `REGIONS` (region name -> row-mask function, rendered via
  `region_charts`) -- edit or add functions there to change what the app
  renders.
- The mapping file persists across months, so the number of unmapped brands
  should shrink over time as you fill it in.
