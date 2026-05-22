# methane-redsense-pipeline

Drone-based multispectral imaging pipeline for mapping methane-relevant vegetation stress at the **W12A landfill** (ITPS Western test site). Raw MicaSense RedEdge-MX captures are radiometrically calibrated in Python, run through WebODM for photogrammetry, then analysed for NDVI / NDRE / custom red-edge ratios over suspected seep zones.

> **Status:** scaffolding only — `src/` package layout exists but no modules are implemented yet. See [CHANGELOG.md](CHANGELOG.md).

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
# Env setup (requirements.txt not yet committed — see CHANGELOG)
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt   # once it exists

# Tests / lint
pytest
ruff check . && ruff format --check .
```

See [CLAUDE.md → Quick commands](CLAUDE.md#quick-commands) for the full pipeline invocations.

## Scope

This repo covers the **multispectral imaging** workstream only. The MQ-series gas-sensor project (Arduino UNO Q / Particle) lives elsewhere — don't merge them.

## Sensor

MicaSense RedEdge-MX, 5 bands: **Blue, Green, Red, NIR, RedEdge**. Band order is load-bearing — never reorder silently.
