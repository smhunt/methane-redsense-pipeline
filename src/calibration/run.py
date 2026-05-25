"""CLI entry point for MicaSense radiometric calibration.

See ``src.calibration.calibrate`` for the underlying logic.

Usage::

    python -m src.calibration.run \\
        --input <flight_dir> --panel <panel.csv> --output <out_dir> [--use-dls]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.calibration import calibrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.calibration.run",
        description=(
            "Calibrate MicaSense RedEdge-MX captures to per-band float32 reflectance "
            "GeoTIFFs (with EXIF preserved from raw for WebODM ingestion)."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Flight directory (walked recursively; panel captures expected under panel/).",
    )
    parser.add_argument(
        "--panel",
        type=Path,
        required=True,
        help="Panel certificate CSV (header: band,reflectance; values 0.0-1.0).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Output directory for calibrated TIFs (mirrors input subdir layout). "
            "If panel detection fails on all panel captures, output is redirected "
            "to <output>_RADIANCE_ONLY/ and contains radiance, not reflectance."
        ),
    )
    parser.add_argument(
        "--use-dls",
        action="store_true",
        help=(
            "Use per-capture DLS2 irradiance instead of panel-derived. "
            "Hard fails if DLS data missing on any capture."
        ),
    )
    args = parser.parse_args(argv)

    if not args.input.is_dir():
        print(f"error: --input is not a directory: {args.input}", file=sys.stderr)
        return calibrate.EXIT_BAD_ARGS

    try:
        summary = calibrate.run_calibration(
            flight_dir=args.input,
            panel_csv_path=args.panel,
            output_dir=args.output,
            use_dls=args.use_dls,
        )
    except calibrate.PanelCSVError as e:
        print(f"error: {e}", file=sys.stderr)
        return calibrate.EXIT_BAD_ARGS
    except calibrate.CalibrationError as e:
        print(f"error: {e}", file=sys.stderr)
        return calibrate.EXIT_BAD_ARGS

    print(calibrate.format_summary(summary))

    if summary.radiance_only:
        return calibrate.EXIT_RADIANCE_ONLY
    if summary.has_failures:
        return calibrate.EXIT_PARTIAL
    return calibrate.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
