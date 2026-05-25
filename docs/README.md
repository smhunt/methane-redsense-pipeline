# Architecture reference

Deeper reference for the W12A methane mapping pipeline. For operating instructions see [PIPELINE.md](PIPELINE.md). For dev instructions / behavioural rules for Claude, see [../CLAUDE.md](../CLAUDE.md).

## System overview

```
                    ┌──────────────────────────────────────────────┐
                    │ Raw flight data (data/MicaSense/flight_<date>/)│
                    │   - 5 bands per capture (.tif, DN)            │
                    │   - CRP panel captures (before / after)       │
                    │   - DLS2 metadata in EXIF                     │
                    └─────────────────────┬────────────────────────┘
                                          │
                          ┌───────────────┴───────────────┐
                          │                               │
                ┌─────────▼─────────┐         ┌───────────▼──────────┐
                │ Path 1 (default)  │         │ Path 2 (quick-look)  │
                │ src.calibration   │         │  (skip Python step)  │
                │   DN → radiance   │         │                      │
                │   (vignette +     │         │                      │
                │    poly coeffs)   │         │                      │
                │   radiance →      │         │                      │
                │    reflectance    │         │                      │
                │   (panel ± DLS2)  │         │                      │
                └─────────┬─────────┘         └───────────┬──────────┘
                          │                               │
              outputs/calibrated/<flight>/        (raw + panels passed through)
              float32 reflectance TIFs,                   │
              EXIF preserved                              │
                          │                               │
                          └──────────┐         ┌──────────┘
                                     ▼         ▼
                          ┌────────────────────────────────┐
                          │ WebODM (src.odm.submit)        │
                          │  - multispectral: true         │
                          │  - texturing-skip-global-      │
                          │    seam-leveling: true         │
                          │  - radiometric-calibration:    │
                          │     none (path 1)              │
                          │     camera+sun (path 2)        │
                          └─────────────────┬──────────────┘
                                            │
                          Per-band orthomosaics
                          (ortho_blue, _green, _red, _nir, _rededge)
                                            │
                          ┌─────────────────▼──────────────┐
                          │ src.analysis.stack_bands       │
                          │  → ortho_stacked.tif           │
                          │    band order: B, G, R, NIR, RE│
                          └─────────────────┬──────────────┘
                                            │
                          ┌─────────────────▼──────────────┐
                          │ src.analysis (indices)         │
                          │  NDVI, NDRE, custom RE ratios  │
                          │  zonal stats over seep zones   │
                          └─────────────────┬──────────────┘
                                            │
                                    outputs/<flight>/
```

## Tech stack

| Layer | Tool / library | Purpose | Status |
|-------|----------------|---------|--------|
| Env mgmt | `uv` | venv + pip | Required for setup |
| Imagery I/O | `rasterio`, GDAL | GeoTIFF read/write, band stacking | TBD (not yet pinned) |
| MicaSense calibration | [`micasense/imageprocessing`](https://github.com/micasense/imageprocessing) | Reference radiometric model | Authoritative |
| EXIF | `pyexiftool` + system `exiftool` | Read XMP/EXIF, preserve through pipeline | TBD |
| Photogrammetry | WebODM (Docker) | Orthomosaic generation per band | `http://localhost:8000` (container `webapp`) |
| Analysis | `numpy`, `rasterio`, possibly `xarray` | Indices, zonal stats | TBD |
| Tests / lint | `pytest`, `ruff` (format + check) | — | Configured in CLAUDE.md, not yet wired |

Nothing in `requirements.txt` yet — pins will be added when the first module lands.

## Calibration model (path 1)

Two ordered steps, both following the [official MicaSense radiometric model](https://github.com/micasense/imageprocessing):

1. **DN → radiance.** Per-pixel:
   - Apply the 6-coefficient radial **vignette polynomial** (cx, cy, k0..k5 from XMP).
   - Apply the **`RadiometricCalibration` polynomial** (a1, a2, a3 from EXIF) plus exposure / ISO / black-level normalisation.
   - Result: radiance in W/m²/sr/nm.
2. **Radiance → reflectance.** Per band, using the CRP panel image:
   - Detect the panel (QR auto-detect; fall back to manual mask).
   - Compute mean panel radiance, divide certificate reflectance by it to get a per-band radiance→reflectance factor.
   - Multiply each image's radiance by that factor.
   - Optional: refine per-image with DLS2 irradiance ratio (only when DLS2 data is clean — clouds or shadowed DLS → fall back to panel-only and flag the flight).

Output: per-band reflectance GeoTIFFs, **float32, 0.0–1.0**, EXIF preserved (GPS, timing, original capture tags) so WebODM can ingest them.

## Key data shapes

### Panel certificate CSV (`data/calibration/panel_<SERIAL>.csv`)

Per-band calibrated reflectance from the MicaSense panel certificate. Reflectance is a **fraction (0.0–1.0)**, not a percentage. Schema:

```csv
band,reflectance
blue,0.520
green,0.519
red,0.518
nir,0.516
rededge,0.517
```

Wavelengths are RedEdge-MX standard; serial in the filename ties the file to the physical panel.

### Reflectance GeoTIFF (per-band output of `src.calibration`)

| Property | Value |
|----------|-------|
| Dtype | `float32` |
| Range | 0.0 – 1.0 (sanity check: >1.2 routinely means broken calibration) |
| Nodata | 0 or NaN, document per-file |
| Geo | Inherited from raw capture EXIF (GPS) |
| EXIF | Copied from raw via `exiftool -TagsFromFile` — required for WebODM alignment |

### Stacked orthomosaic (`ortho_stacked.tif`)

5-band float32 GeoTIFF, **band order: B, G, R, NIR, RedEdge**. Band order is documented in the filename or a sidecar JSON. Never reorder silently — downstream index code assumes this exact order.

## Directory contract

- `data/` and `outputs/` are gitignored — symlink to local copies of the Drive folders / WebODM project dirs.
- `notebooks/` is scratch — nothing in it is authoritative. Promote to `src/` when stable.
- `src/<subpackage>/test_*.py` for tests, run via `pytest`.

## Known gotchas

These are the failure modes that have actually bitten people on RedEdge + WebODM workflows. See [../CLAUDE.md → Known gotchas](../CLAUDE.md#known-gotchas-read-before-debugging) for the full list. Headline items:

- **WebODM banding/striping with RedEdge** → switch to path 1, don't tune around it.
- **EXIF stripped during preprocessing** → WebODM loses GPS, alignment collapses. Always `exiftool -TagsFromFile` to preserve.
- **Panel reflectance entered as percent** → reflectance outputs end up 50× too large. Sanity check 0.0–1.0.
- **DLS2 ≠ panel by >10%** → don't average; prefer panel-only, log it.
- **Mixed RedEdge-MX + RedEdge-P captures** in one project → don't. Band definitions differ.

## Related external references

- MicaSense reference implementation: <https://github.com/micasense/imageprocessing>
- OpenDroneMap docs (WebODM options): <https://docs.opendronemap.org/>
- MicaSense radiometric-calibration tutorial (in `imageprocessing` repo `MicaSense Image Processing Tutorial 1.ipynb`)
