# Changelog

All notable changes to this project will be documented in this file. Format roughly follows [Keep a Changelog](https://keepachangelog.com/) with semantic versioning.

## [0.4.1] - 2026-05-25

### Changed
- Documented the real WebODM endpoint: `http://localhost:8000` (Docker container `webapp` from `webodm/webodm_webapp`; NodeODM worker `node-odx-1` runs alongside). Verified reachable (redirects to `/login/`).
- `CLAUDE.md` → WebODM workflow, `docs/README.md` → tech stack, `docs/PIPELINE.md` §5 all updated to point at the real endpoint instead of TBD.
- `~/.claude/PORTS.md` (global, not in repo) registers WebODM at port 8000 under Reserved Docker Services + adds a `methane-redsense-pipeline` project section.

### Pending
- The `src/odm/submit` helper (next module) will read `WEBODM_URL` from env, defaulting to the registered endpoint.
- No Traefik proxy for WebODM yet; `https://webodm.dev.ecoworks.ca` would be a separate traefik route if we want HTTPS via the usual convention.

## [0.4.0] - 2026-05-25

### Added
- `src/calibration/calibrate.py` — MicaSense radiometric calibration pipeline. Parses panel certificate CSV (with percent-vs-fraction validation), discovers panel + working captures (panel under `<flight>/panel/`, working elsewhere), derives per-band irradiance from panel captures (averaged across all panels that detect on all 5 bands), and calibrates each working capture to per-band float32 reflectance GeoTIFFs with EXIF preserved from raw.
- `src/calibration/run.py` — thin CLI: `python -m src.calibration.run --input <flight_dir> --panel <panel.csv> --output <out_dir> [--use-dls]`. Exit codes: `0` clean / `1` partial / `2` bad args / `3` radiance-only fallback.
- `src/calibration/test_calibrate.py` — 31 tests covering pure logic (CSV parse + validation, capture grouping incl. SET counter collisions, panel/working discovery, float32 TIF roundtrip incl. no-clip behavior, band stats with NaN handling + warning threshold, CLI error paths).

### Behavior worth knowing
- **Output mirrors input subdir layout** (`<output>/0000SET/000/IMG_0001_1.tif`) — chosen over flat naming to avoid MicaSense's per-SET capture-counter collisions.
- **Radiance-only fallback:** if panel detection fails on all panel captures, output is redirected to a sibling `<output>_RADIANCE_ONLY/` directory and the run exits `3`. Prevents accidentally feeding uncalibrated radiance to WebODM.
- **EXIF copy uses explicit allowlist** (`-EXIF:All -XMP:All -MakerNotes:All`) — a bare `-TagsFromFile -overwrite_original` would clobber the GeoTIFF tags rasterio just wrote. Each output is re-opened post-write to confirm rasterio's structure survived.
- **Memory model:** ~25MB resident per `Capture`; explicit `del capture; gc.collect()` per iteration so 1000+ capture flights don't balloon RSS.
- **`--use-dls` hard-fails** if DLS data missing on any capture (operator explicitly opted in; silent fallback to panel would erode the contract).
- **Panel-vs-DLS disagreement >10% warned in summary** (CLAUDE.md gotcha).
- **No clipping** of reflectance values — values >1.2 stay visible in the float32 output and are flagged in the per-band stats so calibration errors don't go silent.

### Changed
- `docs/PIPELINE.md` §4 rewritten to match the real CLI: documents `--use-dls`, the radiance-only fallback dir, exit codes, and the pass criteria the summary actually prints.

## [0.3.0] - 2026-05-22

### Added
- `src/preprocess/exif_audit.py` — first real pipeline module. Walks a flight directory for `IMG_*.tif` captures, reads EXIF/XMP via `pyexiftool`, and reports which captures are missing tags the calibration pipeline requires (RadiometricCalibration, vignette polynomial + center, GPS lat/lon/alt, BandName). DLS2 Irradiance is checked separately as optional with a note about path-1 vs path-2 implications.
- `src/preprocess/test_exif_audit.py` — 12 tests covering filename parsing, group-insensitive tag matching (handles both `EXIF:GPSLatitude` and bare `GPSLatitude` since pyexiftool's prefix varies), file discovery, report formatting, and CLI error paths. Pure logic only; the exiftool integration is exercised in the field.
- Module is invokable as `python -m src.preprocess.exif_audit <flight_dir>`. Exit codes: 0 = all pass, 1 = missing tags or empty dir, 2 = bad path.

### Notes
- `band_from_filename` requires the full `IMG_<capture>_<band>` shape — a bare `IMG_0000.tif` returns `None` instead of misreading the capture index as a band. (A test caught this on the first run.)

## [0.2.1] - 2026-05-22

### Added
- `micasense` runtime dep, installed from the official reference repo and pinned to master @ `3a90386` (the repo is not on PyPI). Pulls in `opencv-python`, `matplotlib`, `pysolar` transitively.
- README and CLAUDE.md document the two env quirks that surfaced when actually building the env: `GIT_LFS_SKIP_SMUDGE=1` for install (orphaned LFS pointers in upstream micasense) and `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` at runtime (pyzbar can't find libzbar via ctypes on macOS arm64).
- `requirements.txt` regenerated against `--python-version 3.11` so the pins match the stated minimum (previous compile against system 3.12 picked `rasterio==1.5.0` which requires 3.12+).

### Changed
- Dropped explicit `opencv-python-headless` from runtime deps. `micasense` already pulls in `opencv-python`; both installed `cv2`, the second-loaded won — wasted disk for no gain. If we ever drop micasense, re-add headless.
- README "Quick start" rewritten as a working step-by-step sequence (system deps → venv → env vars → install → test).

### Verified
- `uv pip install -r requirements.txt` + `uv pip install -e ".[dev]"` succeeds on Python 3.11.14 / macOS arm64.
- All runtime imports (numpy, rasterio, cv2, skimage, pyzbar, exiftool, pyodm, yaml, micasense.{image,capture,panel,dls}) load cleanly with the documented env vars.
- `ruff check .` and `ruff format --check .` pass on the current tree.
- `pytest` runs (0 tests collected — expected; no test files yet).

## [0.2.0] - 2026-05-22

### Added
- `pyproject.toml` as the source-of-truth dependency manifest: runtime deps (numpy, rasterio, opencv-python-headless, scikit-image, pyzbar, pyexiftool, pyodm, pyyaml), `dev` optional-dependencies (pytest, pytest-cov, ruff), ruff and pytest tool config, setuptools build backend, and `src*` package discovery.
- `requirements.txt` pinned via `uv pip compile pyproject.toml -o requirements.txt`.

### Changed
- README "Quick start" now shows the real install (`uv pip install -r requirements.txt` + `uv pip install -e ".[dev]"`) and lists the macOS system deps (`gdal`, `exiftool`, `zbar`).
- CLAUDE.md "Repository status" reflects pyproject + pinned requirements; added the regenerate-from-pyproject command.

### Notes
- MicaSense `imageprocessing` is treated as a *reference* (we follow its model in our own code), not a runtime dep — see comments in `pyproject.toml`.

## [0.1.0] - 2026-05-22

### Added
- Initial `CLAUDE.md` documenting the two processing paths (MicaSense Python calibration → WebODM vs. WebODM-direct), repo conventions, calibration model, WebODM settings, and known gotchas.
- `src/` package layout (`calibration`, `preprocess`, `odm`, `analysis`) with empty `__init__.py` per subpackage — no modules implemented yet.
- `notebooks/` directory placeholder.
- `.gitignore` excluding `data/`, `outputs/`, `.venv/`, Python caches, editor metadata, and `.claude/settings.local.json`.
- `README.md`, `CHANGELOG.md`, `docs/README.md` (architecture), `docs/PIPELINE.md` (operator runbook).

### Notes
- Repo is scaffolding only. No `requirements.txt`, `pyproject.toml`, or working modules yet.
- RedEdge-MX panel serial and certificate CSV still TBD (see `CLAUDE.md` → Open questions).
- WebODM endpoint not yet stood up for this project.
