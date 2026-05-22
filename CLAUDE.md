# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> W12A Methane Project — Sean's landfill methane mapping pipeline. Read this top-to-bottom before touching anything in the repo.

-----

## Repository status

**Status (2026-05-22):** scaffolding only. The `src/` layout described under "Repo conventions" is the *target* structure; nothing has been implemented yet. Create directories as you add the first script for each subsystem. There is no `requirements.txt`, `pyproject.toml`, or test suite in the repo yet.

-----

## Project goal

Map and quantify methane-relevant surface signals at the **W12A landfill** (ITPS Western test site) using drone-based **MicaSense multispectral imagery**, processed through a **MicaSense radiometric calibration pipeline (Python)** and then a **WebODM photogrammetry workflow** to produce calibrated reflectance orthomosaics and vegetation/stress indices (NDVI, NDRE, and custom band ratios sensitive to vegetation stress over methane seep zones).

The MQ-series gas sensor work (Arduino UNO Q / Particle) is a **separate project** and is out of scope here. Do not pull it into this repo or this CLAUDE.md’s scope.

## Primary processing targets (in priority order)

1. **MicaSense → Python calibration → WebODM** — full controlled pipeline. Raw RedEdge DN images are converted to reflectance using the MicaSense `imageprocessing` reference implementation (vignette correction, radiance, panel-based reflectance, optional DLS2 irradiance), then fed into WebODM with `radiometric-calibration none` so WebODM does pure photogrammetry on already-calibrated inputs. This is the **research-grade path** and the default for any analysis that needs defensible reflectance values.
2. **WebODM reflectance orthos direct from MicaSense** — quicker turnaround, useful for QA flights and visual checks. Use WebODM's built-in `camera+sun` radiometric calibration with CRP panel images included in the dataset. Acceptable for visual/qualitative work; do **not** use for quantitative reflectance comparisons across flights.

If a task is ambiguous about which path to use, ask. Don’t default to path 2 silently.

-----

## Repo conventions

- Python 3.11+. Use `uv` for env management (`uv venv`, `uv pip install -r requirements.txt`).
- Source layout:
  - `src/calibration/` — MicaSense radiometric pipeline (vignette, radiance, reflectance, panel detection)
  - `src/preprocess/` — EXIF audit, band stacking, mission/panel splitting
  - `src/odm/` — WebODM task options, settings YAML, batch submission helpers
  - `src/analysis/` — NDVI/NDRE/custom indices, zonal stats, methane-signal heuristics
  - `notebooks/` — exploratory work only; nothing in `notebooks/` is canonical
  - `data/` — **gitignored**; symlink to local copies of the Drive folders
  - `outputs/` — orthomosaics, indices, reports (gitignored)
- Tests live next to code as `test_*.py`, run with `pytest`.
- Format with `ruff format`, lint with `ruff check`. No black, no flake8.

-----

## Data sources (Google Drive: `W12A-FilesFromS...`)

Mirror the relevant subfolders into `data/` locally before processing. Do not commit raw imagery.

|Drive subfolder                      |Contents                                                                                           |Used by this project?                   |
|-------------------------------------|---------------------------------------------------------------------------------------------------|----------------------------------------|
|`MicaSense/`                         |RedEdge-MX raw `.tif` per band (1–5), CRP panel captures before/after flight, DLS2 metadata in EXIF|**Yes — primary input**                 |
|`h20T-data/`                         |DJI H20T thermal/visible captures                                                                  |Secondary — thermal cross-reference only|
|`gopro/`                             |GoPro ground-level footage                                                                         |Field documentation, not processed      |
|`iphone17promaxfootage/`             |iPhone 17 Pro Max footage                                                                          |Field documentation, not processed      |
|`SeansMavic3DroneFootage/`           |Mavic 3 RGB video/photos                                                                           |Site context / fly-overs                |
|`W12A-Test-ITPS-Western-202603...zip`|Packaged dataset, shared                                                                           |Reference / archival                    |

-----

## MicaSense calibration pipeline — key facts

- **Sensor**: MicaSense RedEdge-MX, 5 bands (Blue, Green, Red, NIR, RedEdge). Band order matters everywhere; never reorder silently.
- **CRP panel**: panel-specific reflectance values come from MicaSense’s per-panel certificate. Store the panel CSV at `data/calibration/panel_<serial>.csv` and reference it by serial in code, never hardcode reflectance numbers.
- **Two-step calibration** (in this order):
  1. **DN → radiance** using EXIF `RadiometricCalibration` polynomial coefficients + vignette model (cx, cy, six polynomial coefficients from XMP).
  2. **Radiance → reflectance** using the calibrated reflectance panel image for each band, optionally refined per-image using DLS2 irradiance.
- **DLS2 handling**: prefer `camera+sun` when DLS2 data is clean. If the flight had clouds passing or the DLS was shadowed, fall back to panel-only reflectance and flag the flight in the run log.
- **Panel detection**: MicaSense’s QR-code-based auto-detection is the default. If it fails, fall back to manual mask. Never silently skip panel calibration — if no panel, the output is radiance, not reflectance, and must be labeled as such.
- **Output**: per-band reflectance GeoTIFFs (float32, 0.0–1.0), preserving original EXIF GPS/timing so WebODM can ingest them.

-----

## WebODM workflow — settings that matter

**WebODM endpoint:** TBD — not yet stood up for this project. When set up, register the port in `~/.claude/PORTS.md` and update this section with the URL (e.g. `https://dev.ecoworks.ca:<port>`). The `src/odm/submit` helper should read the endpoint from env (`WEBODM_URL`) rather than hardcoding it.

When feeding **pre-calibrated reflectance TIFs** (path 1) to WebODM, use:

```yaml
radiometric-calibration: none      # we already did it; don't touch values
multispectral: true
texturing-skip-global-seam-leveling: true   # critical: prevents reflectance from being rebalanced across mosaic
feature-quality: high
pc-quality: high
orthophoto-resolution: 8           # cm/px, match your GSD
orthophoto-cutline: true
dem-resolution: 8
```

When feeding **raw MicaSense imagery + CRP panels** (path 2) to WebODM:

```yaml
radiometric-calibration: camera+sun
multispectral: true
texturing-skip-global-seam-leveling: true
```

Either way, WebODM outputs **one orthophoto per band**. To stack into a multi-band image for analysis use:

```bash
gdal_merge.py -separate -o ortho_stacked.tif \
  ortho_blue.tif ortho_green.tif ortho_red.tif ortho_nir.tif ortho_rededge.tif
```

Band order in the stack: **B, G, R, NIR, RedEdge**. Document it in the output filename or sidecar.

-----

## Known gotchas (read before debugging)

- **WebODM banding/striping with RedEdge**: well-documented in the OpenDroneMap community. If banding shows up on path 2, switch to path 1 — don’t try to tune around it.
- **Panel image hygiene**: panel captures must be at the recommended altitude, with the QR fully visible and no shadow from the operator or drone. Bad panel captures silently produce bad reflectance.
- **EXIF preservation**: if any preprocessing step strips EXIF, WebODM loses GPS and the alignment collapses. Always copy EXIF from raw → calibrated TIF using `exiftool -TagsFromFile`.
- **Reflectance value range**: should be 0.0–1.0. Anything routinely >1.2 means calibration is wrong (usually panel reflectance entered as percent instead of fraction, or a bad panel match).
- **DLS2 vs panel disagreement**: if DLS2-corrected reflectance and panel-only reflectance disagree by more than ~10%, log it and prefer panel-only. Don’t average them.
- **Mixing camera generations**: don’t mix RedEdge-MX and RedEdge-P captures in one project; the band definitions differ.

-----

## How Claude should behave on this project

- **Ask before assuming a processing path.** Path 1 vs path 2 changes everything downstream.
- **Never invent reflectance values, panel serials, or EXIF tag names.** If a tag or coefficient isn’t visible in the file, read it with `exiftool` or the MicaSense Python helpers first.
- **Cite the MicaSense radiometric model** when generating calibration code — implementation follows the official model (radial vignette, polynomial coefficients, panel reflectance). The reference repo is `github.com/micasense/imageprocessing`.
- **Don’t reorder bands.** B, G, R, NIR, RedEdge — everywhere.
- **Flag any flight without valid panel captures.** Output is radiance, not reflectance, and must be labeled accordingly. Do not let unlabeled radiance products escape into `outputs/`.
- **Prefer small, composable scripts over monolithic notebooks.** Notebooks are scratch.
- **For large datasets (tens of GB of TIFs), don’t try to process in a sandbox.** Generate a script the user runs locally and report what to expect.
- **Keep the MQ gas sensor project out of scope.** If asked to integrate it, push back and ask whether to start a new repo or whether they really want to merge scopes.

## Quick commands

```bash
# Set up env
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Run all tests
pytest

# Run a single test file or test
pytest src/calibration/test_vignette.py
pytest src/calibration/test_vignette.py::test_radial_model

# Lint + format check (CI-equivalent)
ruff check . && ruff format --check .

# Auto-fix
ruff check --fix . && ruff format .

# Audit a flight's EXIF before processing
python -m src.preprocess.exif_audit data/MicaSense/flight_2026_05_22/

# Run full calibration on a flight
# NOTE: <PANEL_SERIAL> is a placeholder — the actual RedEdge-MX panel serial
# and certificate CSV have not yet been added to data/calibration/.
# See "Open questions / TODO" below.
python -m src.calibration.run \
  --input data/MicaSense/flight_2026_05_22/ \
  --panel data/calibration/panel_<PANEL_SERIAL>.csv \
  --output outputs/calibrated/flight_2026_05_22/

# Submit to local WebODM (path 1)
python -m src.odm.submit \
  --input outputs/calibrated/flight_2026_05_22/ \
  --preset path1_precalibrated \
  --name w12a_2026_05_22

# Stack per-band orthos into single multi-band TIF
python -m src.analysis.stack_bands outputs/odm/w12a_2026_05_22/
```

## Open questions / TODO

- [ ] Confirm RedEdge-MX panel serial and store certificate CSV in `data/calibration/`.
- [ ] Decide on canonical methane-signal index (NDVI baseline, NDRE, or a custom red-edge ratio) — needs literature review against landfill methane vegetation stress studies.
- [ ] Cross-reference H20T thermal anomalies with MicaSense vegetation stress zones (separate notebook for now).
- [ ] Decide whether to standardize on a single GSD across flights for time-series comparison.

-----

*Last updated: 2026-05-22*