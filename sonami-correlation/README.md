# SONAMI Methane Correlation

Correlate **TDLAS** (Tunable Diode Laser Absorption Spectroscopy) point methane
measurements against **Pix4D** orthomosaic imagery over the W12A landfill, to
test whether multispectral/RGB surface signal tracks measured methane.

This is a **separate pipeline** from the MicaSense → WebODM calibration pipeline
that the rest of this repository implements. It lives in its own self-contained
folder with its own dependencies and its own config, and it **shares** the
repo's `data/` root rather than copying imagery. Nothing here imports from
`src/` and `src/` does not import from here — the two pipelines stay decoupled.

> Status: **scaffolding**. Module structure, config schema, CLI, and tested
> analysis functions exist. Every dataset-specific value (file paths, CRS, GSD,
> sample counts, DB credentials) is intentionally left as a `null`/TODO in the
> config — no values are invented. Fill those in before a real run.

---

## Where things live

```
sonami-correlation/
├── README.md
├── pyproject.toml              # this project's own deps (geopandas, rasterio, scipy, folium, …)
├── config/
│   └── sonami.example.yaml     # copy → sonami.yaml, fill in the TODOs
├── sonami/
│   ├── config.py               # load + validate YAML; null fields error loudly when needed
│   ├── data_prep.py            # Phase 1: TDLAS→PostGIS, CRS validation, load Pix4D raster
│   ├── spatial_match.py        # Phase 2: sample raster at points, IDW/kriging grid, align
│   ├── correlation.py          # Phase 3: Pearson/Spearman, Moran's I, R²/p
│   ├── visualize.py            # Phase 4: folium overlay, GeoJSON export, stat plots
│   ├── cli.py                  # `python -m sonami.cli <prep|match|correlate|visualize|run-all>`
│   └── test_*.py               # pytest, run on synthetic data (no real data needed)
└── (data/, outputs/ are shared/gitignored — see below)
```

## Sharing data without duplicating it

Per the repo `CLAUDE.md`, `data/` is gitignored and symlinked to local Drive
mirrors. This project reads from that **same** root — it does not keep its own
copy.

- `config.data.root` defaults to `../data`, i.e. the repo's existing `data/`.
- Put SONAMI inputs under that shared root, e.g.:
  - `data/SONAMI/tdlas/<flight>.csv`
  - `data/SONAMI/pix4d/<flight>_ortho.tif`
- Outputs go to `outputs/sonami/` (already covered by the repo's `outputs/`
  gitignore).

### Working in a separate folder (git worktree)

If you want this project checked out as a **separate working directory** on your
machine while sharing the same data (the original request), use a worktree off
this branch and symlink the shared data root so imagery is never copied:

```bash
# from the main checkout
git worktree add ../sonami-correlation-wt claude/sonami-methane-correlation-ishjc
cd ../sonami-correlation-wt/sonami-correlation

# share the data root instead of duplicating (~tens of GB of TIFs)
ln -s /absolute/path/to/methane-redsense-pipeline/data ./_shared_data
# then set  data.root: "./_shared_data"  in sonami.yaml
```

> Note: this remote/web container is ephemeral — only committed files survive a
> session, so a sibling worktree created here would not persist. The worktree
> step above is for your local machine.

## Setup

```bash
cd sonami-correlation
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# system deps (same family as the parent pipeline)
#   GDAL      → rasterio/geopandas      brew install gdal
#   PostGIS   → psycopg2                brew install postgresql postgis
```

## Configure

```bash
cp config/sonami.example.yaml config/sonami.yaml
# edit config/sonami.yaml — fill in every TODO/null:
#   - analysis_crs (a projected CRS, e.g. UTM, for metric distance ops)
#   - data.tdlas.csv / .crs / column mapping
#   - data.pix4d.orthomosaic / bands / gsd_cm / processing_date
#   - spatial_match.grid_res_m
# PostGIS credentials come from env, not the file:
export SONAMI_PG_HOST=localhost SONAMI_PG_DB=sonami SONAMI_PG_USER=sonami
export SONAMI_PG_PASSWORD=…   # never commit this
```

## Run

```bash
# one phase at a time
python -m sonami.cli prep       --config config/sonami.yaml
python -m sonami.cli match      --config config/sonami.yaml
python -m sonami.cli correlate  --config config/sonami.yaml
python -m sonami.cli visualize  --config config/sonami.yaml

# or end to end
python -m sonami.cli run-all    --config config/sonami.yaml
```

## Pipeline phases

| Phase | Module            | What it does |
|-------|-------------------|--------------|
| 1 Prep      | `data_prep.py`    | Load TDLAS CSV → GeoDataFrame → PostGIS (geometry-indexed); validate CRS consistency; open Pix4D GeoTIFF. |
| 2 Match     | `spatial_match.py`| Sample raster band values at each TDLAS point; build a gridded TDLAS surface (IDW, optional kriging); align extent/resolution to the ortho. |
| 3 Correlate | `correlation.py`  | Pearson & Spearman (point-to-pixel) with R²/p; Moran's I spatial autocorrelation; per-band correlation table. |
| 4 Visualize | `visualize.py`    | Folium map (TDLAS points + ortho composite), highlight high-correlation zones, export GeoJSON, statistical plots. |

## Layout previews

Two self-contained HTML docs under [`docs/`](docs/) show the shape of the Phase 4
report before any real data exists. Open them in a browser.

- [`docs/preview.html`](docs/preview.html) — the rendered Phase 4 report
  (correlation map, Moran's I, per-band table, best-band scatter).
  **All numbers are synthetic**, computed in-browser from one seeded methane
  field so the views stay mutually consistent — they demonstrate the layout and
  the statistics produced, not any W12A result.
- [`docs/wireframes.html`](docs/wireframes.html) — a low-fidelity monochrome
  blueprint of the same page (desktop + mobile), with a numbered key mapping each
  region back to its module (`correlate_bands`, `morans_i`, `correlation_map`,
  `scatter_plot`).

A real run replaces the preview with an interactive folium `correlation_map.html`
plus `correlation_points.geojson` and `scatter_<band>.png`, populated with
measured values.

## Caveats / assumptions to record

- **CRS for distance math**: IDW and Moran's I need a *projected* CRS (metres).
  WGS84 degrees will distort distances. Set `analysis_crs` to the site UTM zone.
- **Temporal alignment**: TDLAS and the Pix4D flight are not simultaneous;
  correlation assumes the surface signal is stable between captures. Note drift.
- **Plume advection / wind**: methane point readings are downwind-displaced from
  the source; expect spatial offset between TDLAS peaks and surface signal.
- **Sensor drift**: log any TDLAS baseline drift over the run in `## Notes`.
