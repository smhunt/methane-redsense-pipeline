# methane-redsense-pipeline

Drone-based multispectral imaging pipeline for mapping methane-relevant vegetation stress at the **W12A landfill** (ITPS Western test site). Raw MicaSense RedEdge-MX captures are radiometrically calibrated in Python, run through WebODM for photogrammetry, then analysed for NDVI / NDRE / custom red-edge ratios over suspected seep zones.

> **Status:** scaffolding — `src/` package layout and dependency manifest in place, no modules implemented yet. See [CHANGELOG.md](CHANGELOG.md).

## Two processing paths

| Path | Calibration | Use case |
|------|-------------|----------|
| **1 — research-grade** (default) | MicaSense Python calibration → WebODM with `radiometric-calibration: none` | Defensible reflectance values, cross-flight comparison, quantitative indices |
| **2 — quick-look** | WebODM `camera+sun` directly on raw MicaSense + CRP panel captures | QA flights, visual checks, qualitative work only |

If a task is ambiguous about which path to use, **ask** — they're not interchangeable downstream.

## Repo layout

```
src/
├── calibration/   MicaSense radiometric pipeline (vignette, radiance, reflectance, panel detection)
├── preprocess/    EXIF audit, band stacking, mission/panel splitting
├── odm/           WebODM task options, settings YAML, batch submission
└── analysis/      NDVI / NDRE / custom indices, zonal stats, methane-signal heuristics
notebooks/         Exploratory work only — not canonical
data/              Gitignored — symlink to local Drive mirror
outputs/           Gitignored — orthomosaics, indices, reports
docs/              Architecture and operator runbook
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — authoritative instructions for the project (read first if you're going to write code here)
- **[docs/README.md](docs/README.md)** — architecture reference (system diagram, tech stack, data shapes)
- **[docs/PIPELINE.md](docs/PIPELINE.md)** — operator runbook for end-to-end flight processing
- **[CHANGELOG.md](CHANGELOG.md)** — version history

## Quick start

```bash
# System deps first (not installable via pip)
brew install gdal exiftool zbar git-lfs

# Env setup
uv venv --python 3.11 && source .venv/bin/activate

# The micasense reference repo has orphaned git-lfs pointers for tutorial
# sample data; the Python module doesn't need them. Skip LFS smudge:
export GIT_LFS_SKIP_SMUDGE=1

uv pip install -r requirements.txt        # runtime only
uv pip install -e ".[dev]"                # + pytest / ruff for development

# pyzbar on Apple Silicon can't find libzbar via ctypes.util.find_library.
# Export this in your shell (and any direnv/.envrc) so Python sees it at import time:
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH

# Tests / lint
pytest
ruff check . && ruff format --check .
```

After changing dependencies in `pyproject.toml`, regenerate the lock:

```bash
GIT_LFS_SKIP_SMUDGE=1 uv pip compile --python-version 3.11 pyproject.toml -o requirements.txt
```

See [CLAUDE.md → Quick commands](CLAUDE.md#quick-commands) for the full pipeline invocations.

## Scope

This repo covers the **multispectral imaging** workstream only. The MQ-series gas-sensor project (Arduino UNO Q / Particle) lives elsewhere — don't merge them.

## Sensor

MicaSense RedEdge-MX, 5 bands: **Blue, Green, Red, NIR, RedEdge**. Band order is load-bearing — never reorder silently.
