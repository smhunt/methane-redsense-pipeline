# Changelog

All notable changes to this project will be documented in this file. Format roughly follows [Keep a Changelog](https://keepachangelog.com/) with semantic versioning.

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
