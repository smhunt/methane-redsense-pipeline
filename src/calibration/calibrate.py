"""MicaSense radiometric calibration: DN → reflectance, per-band float32 GeoTIFFs.

Reads raw MicaSense RedEdge-MX captures, computes reflectance via the
panel-based (default) or DLS2-refined model (``use_dls=True``), and writes
per-band float32 GeoTIFFs with EXIF preserved from the raw captures so
WebODM can ingest them with ``--radiometric-calibration none``.

See ``CLAUDE.md`` and ``docs/README.md`` for the calibration model and
data shapes this module produces.
"""

from __future__ import annotations

import csv
import dataclasses
import gc
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import exiftool
import micasense.capture
import numpy as np
import rasterio

EO_BANDS_DEFAULT: tuple[str, ...] = ("Blue", "Green", "Red", "NIR", "Red edge")
_PANEL_SUBDIR = "panel"
_IMG_PATTERN = re.compile(r"^IMG_(\d+)_(\d+)$")

# Exit codes (re-exported via run.py)
EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BAD_ARGS = 2
EXIT_RADIANCE_ONLY = 3

# Explicit allowlist for exiftool tag copy. Avoids clobbering rasterio's
# GeoTIFF tags (which a bare -TagsFromFile -overwrite_original would do).
EXIFTOOL_TAG_COPY_ARGS: tuple[str, ...] = ("-EXIF:All", "-XMP:All", "-MakerNotes:All")


class PanelCSVError(ValueError):
    """Raised when the panel certificate CSV is missing, malformed, or out of range."""


class CalibrationError(RuntimeError):
    """Raised when calibration cannot proceed (missing panels, bad args, etc.)."""


@dataclasses.dataclass(frozen=True)
class BandStats:
    band_name: str
    mean: float
    p95: float
    max: float

    @property
    def warning(self) -> str | None:
        # CLAUDE.md gotcha: routinely >1.2 means calibration is wrong.
        if np.isfinite(self.max) and self.max > 1.2:
            return f"max {self.max:.3f} > 1.2 (calibration suspect)"
        return None


@dataclasses.dataclass(frozen=True)
class CaptureResult:
    capture_paths: tuple[Path, ...]
    out_paths: tuple[Path, ...]
    band_stats: tuple[BandStats, ...]
    ok: bool
    error: str | None = None
    dls_irradiance: tuple[float, ...] | None = None


@dataclasses.dataclass(frozen=True)
class CalibrationSummary:
    flight_dir: Path
    output_dir: Path
    radiance_only: bool
    panel_irradiance: tuple[float, ...] | None
    dls_irradiance_mean: tuple[float, ...] | None
    band_names: tuple[str, ...]
    captures_processed: int
    captures_failed: int
    failed: tuple[CaptureResult, ...]
    per_band_summary: dict[str, BandStats]

    @property
    def has_failures(self) -> bool:
        return self.captures_failed > 0


# ─── Panel CSV ──────────────────────────────────────────────────────


def parse_panel_csv(path: Path) -> dict[str, float]:
    """Parse a ``band,reflectance`` CSV → ``dict``.

    Band names are lowercased for case-insensitive lookup. Reflectance must
    be a fraction in ``(0.0, 1.0]`` — values > 1.0 raise ``PanelCSVError``
    to catch the percent-vs-fraction gotcha CLAUDE.md flags.
    """
    if not path.is_file():
        raise PanelCSVError(f"Panel CSV not found: {path}")

    result: dict[str, float] = {}
    with path.open() as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if "band" not in fields or "reflectance" not in fields:
            raise PanelCSVError(f"Panel CSV must have header 'band,reflectance'; got {fields}")
        for row in reader:
            band = (row.get("band") or "").strip().lower()
            if not band:
                continue
            try:
                refl = float(row.get("reflectance") or "")
            except ValueError as e:
                raise PanelCSVError(
                    f"Non-numeric reflectance for band '{band}': {row.get('reflectance')!r}"
                ) from e
            if refl <= 0 or refl > 1.0:
                raise PanelCSVError(
                    f"Panel reflectance for '{band}' = {refl} outside (0, 1.0]. "
                    "Values must be a fraction (0.52, not 52)."
                )
            result[band] = refl

    if not result:
        raise PanelCSVError(f"Panel CSV {path} has no data rows.")
    return result


def panel_csv_lookup(csv_data: dict[str, float], band_names: Iterable[str]) -> list[float]:
    """Return reflectances in ``band_names`` order. Case-insensitive, space-stripping.

    micasense's ``Capture.eo_band_names()`` returns e.g. ``["Blue", "Green",
    "Red", "NIR", "Red edge"]`` for RedEdge-MX — note ``"Red edge"`` with a
    space, which we normalize to ``"rededge"`` to match the CSV convention.
    """
    out: list[float] = []
    missing: list[str] = []
    for name in band_names:
        key = name.strip().lower().replace(" ", "")
        if key not in csv_data:
            missing.append(name)
        else:
            out.append(csv_data[key])
    if missing:
        raise PanelCSVError(
            f"Panel CSV missing reflectance for band(s): {missing}. "
            f"Available: {sorted(csv_data.keys())}"
        )
    return out


# ─── Discovery & grouping ───────────────────────────────────────────


def _band_from_stem(stem: str) -> int | None:
    m = _IMG_PATTERN.match(stem)
    return int(m.group(2)) if m else None


def _capture_key(path: Path) -> tuple[Path, str] | None:
    """Return ``(parent_dir, IMG_xxxx)`` for grouping, or ``None`` if not parseable.

    Captures in different SET subdirectories must group separately — MicaSense
    restarts the capture counter per SET, so the same ``IMG_0001`` in
    ``0000SET/000/`` and ``0000SET/001/`` are different captures.
    """
    m = _IMG_PATTERN.match(path.stem)
    if not m:
        return None
    return (path.parent, f"IMG_{m.group(1)}")


def group_by_capture(paths: Iterable[Path]) -> list[list[Path]]:
    """Group ``IMG_xxxx_N.tif`` paths by ``(directory, capture_index)``.

    Each group is sorted by band number. Partial groups (<5 bands) are
    returned as-is — callers decide whether to skip them.
    """
    groups: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    for p in paths:
        key = _capture_key(p)
        if key is not None:
            groups[key].append(p)
    return [
        sorted(groups[k], key=lambda p: _band_from_stem(p.stem) or 0)
        for k in sorted(groups.keys(), key=lambda k: (str(k[0]), k[1]))
    ]


def find_panel_captures(flight_dir: Path) -> list[list[Path]]:
    """``IMG_*.tif`` under ``<flight_dir>/panel/`` (recursively), grouped by capture."""
    panel_dir = flight_dir / _PANEL_SUBDIR
    if not panel_dir.is_dir():
        return []
    return group_by_capture(p for p in panel_dir.rglob("IMG_*.tif") if p.is_file())


def find_working_captures(flight_dir: Path) -> list[list[Path]]:
    """``IMG_*.tif`` under ``flight_dir`` but NOT under ``panel/``, grouped by capture."""
    panel_root = flight_dir / _PANEL_SUBDIR
    panel_resolved = panel_root.resolve() if panel_root.exists() else None

    candidates: list[Path] = []
    for p in flight_dir.rglob("IMG_*.tif"):
        if not p.is_file():
            continue
        if panel_resolved is not None:
            try:
                p.resolve().relative_to(panel_resolved)
                continue  # under panel/, skip
            except ValueError:
                pass
        candidates.append(p)
    return group_by_capture(candidates)


# ─── Panel irradiance derivation ───────────────────────────────────


def derive_panel_irradiance(
    panel_groups: list[list[Path]],
    panel_csv: dict[str, float],
) -> tuple[list[float], list[str]] | None:
    """Derive per-band irradiance by averaging across panel captures.

    For each panel capture, build a ``Capture``, detect the panel on all
    bands, and compute irradiance from panel radiance + CSV reflectance.
    Panel captures where ``detect_panels()`` returns fewer than the EO band
    count are dropped (mixed-success captures pollute the average). Returns
    ``(per_band_irradiance, band_names)`` or ``None`` if no panel capture
    survived — in which case the caller switches to radiance-only output.
    """
    samples: list[list[float]] = []
    band_names: list[str] | None = None

    for group in panel_groups:
        if len(group) < 5:
            continue
        capture = micasense.capture.Capture.from_filelist([str(p) for p in group])
        try:
            if band_names is None:
                band_names = list(capture.eo_band_names())
            n_detected = capture.detect_panels()
            n_eo = len(capture.eo_band_names())
            if n_detected < n_eo:
                continue
            reflectances = panel_csv_lookup(panel_csv, band_names)
            samples.append(list(capture.panel_irradiance(reflectances=reflectances)))
        finally:
            del capture
            gc.collect()

    if not samples or band_names is None:
        return None
    avg = [float(np.mean([s[i] for s in samples])) for i in range(len(band_names))]
    return avg, band_names


# ─── Per-capture calibration + I/O ─────────────────────────────────


def write_reflectance_tif(arr: np.ndarray, out_path: Path) -> None:
    """Write a 2-D float32 ndarray as a single-band GeoTIFF (no CRS).

    MicaSense raw imagery has no CRS — WebODM uses EXIF GPS for alignment,
    so omitting CRS is correct. Values are written as-is; no scaling or
    clipping (CLAUDE.md: routinely >1.2 should stay visible as a calibration
    warning, not be silently clamped).
    """
    if arr.ndim != 2:
        raise ValueError(f"Expected 2-D ndarray, got shape {arr.shape}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        count=1,
        dtype="float32",
        height=arr.shape[0],
        width=arr.shape[1],
        compress="deflate",
    ) as dst:
        dst.write(arr.astype("float32"), 1)


def _copy_exif(et: exiftool.ExifToolHelper, src: Path, dest: Path) -> None:
    et.execute(
        "-TagsFromFile",
        str(src),
        *EXIFTOOL_TAG_COPY_ARGS,
        "-overwrite_original",
        str(dest),
    )


def _band_stats(arr: np.ndarray, band_name: str) -> BandStats:
    flat = arr[np.isfinite(arr)] if arr.size else arr
    if flat.size == 0:
        nan = float("nan")
        return BandStats(band_name=band_name, mean=nan, p95=nan, max=nan)
    return BandStats(
        band_name=band_name,
        mean=float(np.mean(flat)),
        p95=float(np.percentile(flat, 95)),
        max=float(np.max(flat)),
    )


def calibrate_one(
    capture_paths: list[Path],
    irradiance_source: list[float] | str,
    input_root: Path,
    output_root: Path,
    et: exiftool.ExifToolHelper,
) -> CaptureResult:
    """Calibrate one capture and write per-band output TIFs.

    ``irradiance_source`` is one of:
      - ``list[float]`` — panel-derived irradiance (constant across all working captures)
      - ``"dls"``       — use this capture's own DLS2 irradiance
      - ``"radiance"``  — skip reflectance, write radiance arrays (fallback mode)

    Output paths mirror the input layout: an input at
    ``input_root/0000SET/000/IMG_0001_1.tif`` lands at
    ``output_root/0000SET/000/IMG_0001_1.tif``. SET counter collisions are
    therefore impossible (different SETs → different output paths).
    """
    capture_paths_t = tuple(capture_paths)
    capture: micasense.capture.Capture | None = None
    try:
        capture = micasense.capture.Capture.from_filelist([str(p) for p in capture_paths])

        dls_used: tuple[float, ...] | None = None
        if irradiance_source == "radiance":
            arrays = [img.radiance() for img in capture.eo_images()]
        elif irradiance_source == "dls":
            if not capture.dls_present():
                raise CalibrationError(
                    f"--use-dls passed but DLS data missing on {capture_paths[0].name}"
                )
            dls_irr = list(capture.dls_irradiance())
            arrays = capture.reflectance(dls_irr)
            dls_used = tuple(dls_irr)
        else:
            arrays = capture.reflectance(list(irradiance_source))

        eo_images = capture.eo_images()
        out_paths: list[Path] = []
        stats: list[BandStats] = []
        for raw_path, img, arr in zip(capture_paths, eo_images, arrays, strict=True):
            out_path = output_root / raw_path.relative_to(input_root)
            write_reflectance_tif(arr, out_path)
            _copy_exif(et, raw_path, out_path)
            # Sanity: rasterio's GeoTIFF structure must survive the exiftool pass.
            with rasterio.open(out_path) as ds:
                if ds.count != 1 or ds.dtypes[0] != "float32":
                    raise CalibrationError(
                        f"GeoTIFF corrupted after EXIF copy: {out_path} "
                        f"(count={ds.count}, dtype={ds.dtypes[0]})"
                    )
            out_paths.append(out_path)
            stats.append(_band_stats(arr, img.band_name))

        return CaptureResult(
            capture_paths=capture_paths_t,
            out_paths=tuple(out_paths),
            band_stats=tuple(stats),
            ok=True,
            dls_irradiance=dls_used,
        )
    except Exception as e:
        return CaptureResult(
            capture_paths=capture_paths_t,
            out_paths=(),
            band_stats=(),
            ok=False,
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        # ~25MB resident per Capture; matters at 1000+ scale.
        if capture is not None:
            del capture
        gc.collect()


# ─── Orchestration ─────────────────────────────────────────────────


def _radiance_only_dir(output_dir: Path) -> Path:
    return output_dir.with_name(output_dir.name + "_RADIANCE_ONLY")


def _aggregate_stats(results: list[CaptureResult], band_names: list[str]) -> dict[str, BandStats]:
    means: dict[str, list[float]] = defaultdict(list)
    p95s: dict[str, list[float]] = defaultdict(list)
    maxes: dict[str, list[float]] = defaultdict(list)
    for r in results:
        if not r.ok:
            continue
        for s in r.band_stats:
            if np.isfinite(s.mean):
                means[s.band_name].append(s.mean)
            if np.isfinite(s.p95):
                p95s[s.band_name].append(s.p95)
            if np.isfinite(s.max):
                maxes[s.band_name].append(s.max)
    out: dict[str, BandStats] = {}
    for b in band_names:
        if not means[b]:
            nan = float("nan")
            out[b] = BandStats(b, nan, nan, nan)
            continue
        out[b] = BandStats(
            band_name=b,
            mean=float(np.mean(means[b])),
            p95=float(np.mean(p95s[b])),
            max=float(np.max(maxes[b])),
        )
    return out


def run_calibration(
    flight_dir: Path,
    panel_csv_path: Path,
    output_dir: Path,
    *,
    use_dls: bool = False,
) -> CalibrationSummary:
    """Top-level orchestration. Returns a ``CalibrationSummary`` for printing."""
    panel_csv = parse_panel_csv(panel_csv_path)

    panel_groups = find_panel_captures(flight_dir)
    if not panel_groups:
        raise CalibrationError(
            f"No panel captures found under {flight_dir / _PANEL_SUBDIR}/. "
            "Place CRP panel captures there before running calibration."
        )

    working_groups = find_working_captures(flight_dir)
    if not working_groups:
        raise CalibrationError(
            f"No working captures (IMG_*.tif outside panel/) found in {flight_dir}."
        )

    panel_result = derive_panel_irradiance(panel_groups, panel_csv)
    radiance_only = panel_result is None
    if radiance_only:
        output_dir = _radiance_only_dir(output_dir)
        panel_irradiance: list[float] | None = None
        band_names = list(EO_BANDS_DEFAULT)
    else:
        panel_irradiance, band_names = panel_result

    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[CaptureResult] = []
    with exiftool.ExifToolHelper() as et:
        for group in working_groups:
            if len(group) < 5:
                results.append(
                    CaptureResult(
                        capture_paths=tuple(group),
                        out_paths=(),
                        band_stats=(),
                        ok=False,
                        error=f"Partial capture: only {len(group)} of 5 bands present",
                    )
                )
                continue

            if radiance_only:
                source: list[float] | str = "radiance"
            elif use_dls:
                source = "dls"
            else:
                assert panel_irradiance is not None
                source = panel_irradiance

            results.append(calibrate_one(group, source, flight_dir, output_dir, et))

    n_failed = sum(1 for r in results if not r.ok)
    n_processed = sum(1 for r in results if r.ok)

    dls_mean: tuple[float, ...] | None = None
    dls_samples = [r.dls_irradiance for r in results if r.dls_irradiance is not None]
    if dls_samples:
        n_bands = len(dls_samples[0])
        dls_mean = tuple(float(np.mean([s[i] for s in dls_samples])) for i in range(n_bands))

    return CalibrationSummary(
        flight_dir=flight_dir,
        output_dir=output_dir,
        radiance_only=radiance_only,
        panel_irradiance=tuple(panel_irradiance) if panel_irradiance else None,
        dls_irradiance_mean=dls_mean,
        band_names=tuple(band_names),
        captures_processed=n_processed,
        captures_failed=n_failed,
        failed=tuple(r for r in results if not r.ok),
        per_band_summary=_aggregate_stats(results, band_names),
    )


def format_summary(s: CalibrationSummary) -> str:
    lines = [
        f"Calibration: {s.flight_dir}",
        f"  Output:    {s.output_dir}",
    ]
    if s.radiance_only:
        lines.append("")
        lines.append("  *** RADIANCE-ONLY MODE ***")
        lines.append("  Panel detection failed on all panel captures.")
        lines.append("  Output is radiance (W/m²/sr/nm), NOT reflectance.")
        lines.append("  Do NOT feed to WebODM with --radiometric-calibration none.")

    lines.append(f"  Captures processed: {s.captures_processed}")
    lines.append(f"  Captures failed:    {s.captures_failed}")

    if s.panel_irradiance is not None:
        lines.append("")
        lines.append("  Panel-derived irradiance (W/m²/nm):")
        for name, ir in zip(s.band_names, s.panel_irradiance, strict=True):
            lines.append(f"    {name:<10s} {ir:.4f}")

    if s.dls_irradiance_mean is not None:
        lines.append("")
        lines.append("  DLS2 mean irradiance across captures (W/m²/nm):")
        for i, (name, ir) in enumerate(zip(s.band_names, s.dls_irradiance_mean, strict=True)):
            disagree = ""
            if s.panel_irradiance is not None:
                panel_ir = s.panel_irradiance[i]
                if panel_ir > 0:
                    pct = abs(ir - panel_ir) / panel_ir * 100
                    if pct > 10:
                        disagree = f"  ** panel-vs-DLS disagreement {pct:.1f}% (>10%) **"
            lines.append(f"    {name:<10s} {ir:.4f}{disagree}")

    if s.per_band_summary and not s.radiance_only:
        lines.append("")
        lines.append("  Reflectance stats (pooled across captures):")
        lines.append(f"    {'band':<10s} {'mean':>8s} {'p95':>8s} {'max':>8s}")
        for name in s.band_names:
            bs = s.per_band_summary.get(name)
            if bs is None:
                continue
            warn = f"  ** {bs.warning} **" if bs.warning else ""
            lines.append(f"    {name:<10s} {bs.mean:>8.4f} {bs.p95:>8.4f} {bs.max:>8.4f}{warn}")

    if s.has_failures:
        lines.append("")
        n_show = min(5, s.captures_failed)
        lines.append(f"  First {n_show} failures:")
        for r in s.failed[:n_show]:
            name = r.capture_paths[0].name if r.capture_paths else "?"
            lines.append(f"    {name:<30s} {r.error}")

    return "\n".join(lines)
