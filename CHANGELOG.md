# Changelog

All notable changes to this project will be documented in this file. Format roughly follows [Keep a Changelog](https://keepachangelog.com/) with semantic versioning.

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
