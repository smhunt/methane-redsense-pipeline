"""EXIF audit for MicaSense RedEdge-MX captures.

Walks a flight directory, reads EXIF/XMP from every ``IMG_*.tif`` capture,
and reports which captures are missing tags the calibration pipeline
requires. Pass criteria mirror ``docs/PIPELINE.md`` § 2.

Usage: ``python -m src.preprocess.exif_audit <flight_dir>``
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections import defaultdict
from pathlib import Path

import exiftool

REQUIRED_TAGS = (
    "RadiometricCalibration",
    "VignettingPolynomial",
    "VignettingCenter",
    "GPSLatitude",
    "GPSLongitude",
    "GPSAltitude",
    "BandName",
)

# DLS2 irradiance is optional: panel-only path-1 calibration still works
# without it (path 2 'camera+sun' will be degraded).
OPTIONAL_TAGS = ("Irradiance",)


@dataclasses.dataclass(frozen=True)
class FileAudit:
    path: Path
    band: int | None
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.missing_required


@dataclasses.dataclass(frozen=True)
class FlightAudit:
    flight_dir: Path
    files: tuple[FileAudit, ...]

    @property
    def n_files(self) -> int:
        return len(self.files)

    @property
    def n_passed(self) -> int:
        return sum(1 for f in self.files if f.passed)


def find_captures(root: Path) -> list[Path]:
    """Recursively find MicaSense capture files (``IMG_*.tif``)."""
    return sorted(p for p in root.rglob("IMG_*.tif") if p.is_file())


def band_from_filename(path: Path) -> int | None:
    """Extract band number from ``IMG_xxxx_N.tif``; returns ``None`` if not parseable.

    Requires the ``IMG_<capture>_<band>`` shape — a bare ``IMG_0000.tif``
    returns ``None`` rather than misreading the capture index as a band.
    """
    parts = path.stem.split("_")
    if len(parts) >= 3 and parts[0] == "IMG" and parts[-1].isdigit():
        return int(parts[-1])
    return None


def check_tags(metadata: dict[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(missing_required, missing_optional)`` for one capture's metadata.

    Group-insensitive: ``EXIF:GPSLatitude`` and ``GPSLatitude`` both satisfy
    ``GPSLatitude``. exiftool may return either depending on version / flags.
    """
    present = {k.rsplit(":", 1)[-1] for k in metadata}
    missing_req = tuple(t for t in REQUIRED_TAGS if t not in present)
    missing_opt = tuple(t for t in OPTIONAL_TAGS if t not in present)
    return missing_req, missing_opt


def audit_flight(flight_dir: Path) -> FlightAudit:
    """Walk ``flight_dir``, read EXIF for every capture, return a :class:`FlightAudit`."""
    captures = find_captures(flight_dir)
    if not captures:
        return FlightAudit(flight_dir=flight_dir, files=())

    with exiftool.ExifToolHelper() as et:
        all_meta = et.get_metadata([str(p) for p in captures])

    files: list[FileAudit] = []
    for path, meta in zip(captures, all_meta, strict=True):
        missing_req, missing_opt = check_tags(meta)
        files.append(
            FileAudit(
                path=path,
                band=band_from_filename(path),
                missing_required=missing_req,
                missing_optional=missing_opt,
            )
        )
    return FlightAudit(flight_dir=flight_dir, files=tuple(files))


def format_report(audit: FlightAudit) -> str:
    """Render a human-readable audit report for the CLI."""
    lines = [f"EXIF audit: {audit.flight_dir}", f"  Files found: {audit.n_files}"]
    if audit.n_files == 0:
        lines.append("  No IMG_*.tif captures found.")
        return "\n".join(lines)

    lines.append(f"  Files passing: {audit.n_passed} / {audit.n_files}")

    missing_req_counts: dict[str, int] = defaultdict(int)
    missing_opt_counts: dict[str, int] = defaultdict(int)
    for f in audit.files:
        for tag in f.missing_required:
            missing_req_counts[tag] += 1
        for tag in f.missing_optional:
            missing_opt_counts[tag] += 1

    if missing_req_counts:
        lines.append("")
        lines.append("  Missing REQUIRED tags (files affected / total):")
        for tag, count in sorted(missing_req_counts.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"    {tag:<28s} {count:>5d} / {audit.n_files}")

    if missing_opt_counts:
        lines.append("")
        lines.append("  Missing OPTIONAL tags (files affected / total):")
        for tag, count in sorted(missing_opt_counts.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"    {tag:<28s} {count:>5d} / {audit.n_files}")
        if "Irradiance" in missing_opt_counts:
            lines.append("")
            lines.append("  Note: DLS2 Irradiance missing on some/all captures. Path 1 panel-only")
            lines.append("        reflectance is unaffected; path 2 (camera+sun) will be degraded.")

    failed = [f for f in audit.files if not f.passed]
    if failed:
        n_show = min(5, len(failed))
        lines.append("")
        lines.append(f"  First {n_show} failing captures:")
        for f in failed[:n_show]:
            band = f"band={f.band}" if f.band is not None else "band=?"
            tags = ", ".join(f.missing_required)
            lines.append(f"    {f.path.name:<30s} {band:<10s} missing: {tags}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.preprocess.exif_audit",
        description="Audit MicaSense capture EXIF for tags the calibration pipeline requires.",
    )
    parser.add_argument(
        "flight_dir",
        type=Path,
        help="Flight directory (walked recursively for IMG_*.tif).",
    )
    args = parser.parse_args(argv)

    if not args.flight_dir.is_dir():
        print(f"error: not a directory: {args.flight_dir}", file=sys.stderr)
        return 2

    audit = audit_flight(args.flight_dir)
    print(format_report(audit))

    if audit.n_files == 0 or audit.n_passed < audit.n_files:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
