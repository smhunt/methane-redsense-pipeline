# Operator runbook — end-to-end flight processing

How to take a W12A MicaSense flight from "SD card just landed on the laptop" to "indices in `outputs/`". Targeted at the operator (Sean or anyone he hands the work to), not at someone modifying the pipeline code. For architecture see [README.md](README.md); for repo rules see [../CLAUDE.md](../CLAUDE.md).

> **Status:** this runbook describes the *intended* flow. Several steps reference modules that are not yet implemented (`src.preprocess.exif_audit`, `src.calibration.run`, `src.odm.submit`, `src.analysis.stack_bands`). Until those land, you'll need to perform the equivalent step manually (e.g. invoke `exiftool` directly, or use the WebODM web UI).

## 0 · Pre-flight checklist (in the field)

Worth a callout because most pipeline failures start here:

- [ ] **CRP panel captures** taken before *and* after the flight, at the recommended altitude, QR fully visible, no operator/drone shadow on the panel.
- [ ] **DLS2** powered, cap off, mounted level. Note conditions (clear / cloud-passing / overcast) in the flight log.
- [ ] **One sensor only.** Don't mix RedEdge-MX and RedEdge-P captures in the same flight.
- [ ] **GSD target documented.** If doing time-series, hit the same altitude / GSD as previous flights.

If any of these are missed, log it. The pipeline can still produce *something*, but you must know what assumptions are broken.

## 1 · Transfer flight data

Copy from the SD card into the local Drive mirror, **not** into the repo. Suggested layout:

```
data/MicaSense/flight_<YYYY_MM_DD>/
  ├── 0000SET/
  │   ├── 000/   ← Captures 0000–9999 per band, .tif
  │   ├── 001/
  │   └── ...
  └── panel/     ← CRP panel captures, segregated for clarity
```

`data/` is gitignored — it's a symlink to wherever you keep raw imagery (Drive, NAS, external SSD).

## 2 · EXIF audit

Confirm the captures actually contain what the pipeline needs (RadiometricCalibration coefficients, vignette polynomial, DLS2 irradiance, GPS):

```bash
python -m src.preprocess.exif_audit data/MicaSense/flight_<YYYY_MM_DD>/
```

Until the module exists, run manually:

```bash
exiftool -G -a -s \
  -XMP-MicaSense:All -XMP-Camera:All \
  -RadiometricCalibration -BlackLevel -ISOSpeed -ExposureTime \
  -GPSLongitude -GPSLatitude -GPSAltitude \
  data/MicaSense/flight_<YYYY_MM_DD>/0000SET/000/IMG_0000_1.tif
```

**Pass criteria:**
- `RadiometricCalibration` present (3 floats).
- Vignette polynomial coefficients present (6 floats + cx, cy).
- GPS lat/lon/alt present.
- DLS2 irradiance present (or note it's missing — affects path decision).

If any are missing on a representative sample, **stop** and figure out why before processing further.

## 3 · Decide path 1 vs path 2

```
Are you doing quantitative analysis or cross-flight comparison?
├── Yes ────────────────────────────────────► Path 1 (research-grade)
└── No (visual QA, single-flight quick look)
    │
    Are panel captures good AND DLS2 clean?
    ├── Yes ──────────────────────────────► Path 2 (WebODM camera+sun)
    └── No ───────────────────────────────► Path 1 (panel-only fallback)
```

When in doubt: **path 1**. The Python step costs minutes, miscalibration costs hours.

## 4 · Calibrate (path 1 only)

```bash
python -m src.calibration.run \
  --input data/MicaSense/flight_<YYYY_MM_DD>/ \
  --panel data/calibration/panel_<PANEL_SERIAL>.csv \
  --output outputs/calibrated/flight_<YYYY_MM_DD>/
```

**Pass criteria:**
- Output is per-band float32 TIF, values in 0.0–1.0.
- Spot-check 3–5 images in QGIS or `rio info` — reflectance histogram should not be clipped at 0 or 1.
- EXIF preserved: `exiftool outputs/calibrated/.../IMG_0000_1.tif | grep GPS` returns coordinates.

**If panel detection failed on all panel captures:**
- The output is radiance, not reflectance. Tag the output directory accordingly (e.g. `flight_<date>_RADIANCE_ONLY/`) and *do not* feed it to downstream index code expecting reflectance.

## 5 · Submit to WebODM

```bash
python -m src.odm.submit \
  --input outputs/calibrated/flight_<YYYY_MM_DD>/ \   # path 1
  --preset path1_precalibrated \
  --name w12a_<YYYY_MM_DD>
```

For path 2, point `--input` at the raw `data/MicaSense/flight_<YYYY_MM_DD>/` directory and use `--preset path2_camerasun`.

The presets bake in the settings from [../CLAUDE.md → WebODM workflow](../CLAUDE.md#webodm-workflow--settings-that-matter). Key flags either preset must set:

- `multispectral: true`
- `texturing-skip-global-seam-leveling: true` ← critical for reflectance integrity
- `radiometric-calibration: none` (path 1) or `camera+sun` (path 2)

**WebODM endpoint:** not yet stood up for this project — see [../CLAUDE.md → WebODM workflow](../CLAUDE.md#webodm-workflow--settings-that-matter) for the env-var contract once it's running.

While processing, expect: hours for a typical W12A flight on local hardware, depending on GSD and image count.

## 6 · Pull orthomosaics and stack

WebODM outputs one orthophoto per band. Stack them into a single multi-band TIF:

```bash
python -m src.analysis.stack_bands outputs/odm/w12a_<YYYY_MM_DD>/
```

Until that module exists, do it manually:

```bash
gdal_merge.py -separate -o ortho_stacked.tif \
  ortho_blue.tif ortho_green.tif ortho_red.tif ortho_nir.tif ortho_rededge.tif
```

**Band order: B, G, R, NIR, RedEdge.** Document it in the filename or a sidecar.

## 7 · Compute indices

```bash
# NDVI, NDRE, etc. — exact CLI TBD.
python -m src.analysis.indices outputs/odm/w12a_<YYYY_MM_DD>/ortho_stacked.tif
```

Formulas (reference):

| Index | Formula | Notes |
|-------|---------|-------|
| NDVI | (NIR − Red) / (NIR + Red) | Baseline veg vigour; saturates in dense canopy |
| NDRE | (NIR − RedEdge) / (NIR + RedEdge) | More sensitive to canopy stress; canonical methane-stress candidate |
| GNDVI | (NIR − Green) / (NIR + Green) | Sometimes used alongside NDRE |
| Custom RE ratio | TBD | See open question in CLAUDE.md |

## 8 · Validation checks (do not skip)

Before trusting any output:

- [ ] **Reflectance range:** ortho histogram per band sits in 0.0–1.0. Routine values >1.2 ⇒ calibration is wrong.
- [ ] **EXIF preserved end-to-end:** GPS visible on calibrated TIFs *and* the WebODM mosaic is georeferenced where you expect it on the W12A footprint.
- [ ] **Panel ≈ DLS2 (if both used):** disagreement >10% on any band ⇒ prefer panel-only and log it.
- [ ] **No banding** on the orthomosaic (visually inspect in QGIS at 1:1). Banding on path 2 ⇒ switch to path 1 and re-run.
- [ ] **Flight log entry:** flight date, path (1/2), panel serial, DLS2 condition note, GSD, anything that went sideways.

## When things go wrong

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| WebODM throws GPS / alignment error | EXIF stripped during preprocessing | Re-copy EXIF with `exiftool -TagsFromFile`, re-submit |
| Reflectance ortho values ~50 | Panel reflectance entered as percent (52 vs 0.52) | Fix the panel CSV, re-run calibration |
| Vertical banding / striping on RGB ortho (path 2) | Known WebODM + RedEdge issue | Switch to path 1 |
| Mosaic has a "patchwork" reflectance look | Global seam levelling was applied | Confirm `texturing-skip-global-seam-leveling: true`, re-run |
| Panel auto-detect fails | Shadow / overexposure / cropped QR | Manual mask; if no salvageable panel image, output is radiance only |
| NDVI / NDRE values clipped at -1 or 1 | Division by ~zero (one band near 0) or bad calibration | Check input reflectance histograms first |
