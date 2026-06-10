"""Command-line entry point.

    python -m sonami.cli <prep|match|correlate|visualize|run-all> --config config/sonami.yaml

Each subcommand runs one pipeline phase; ``run-all`` chains them. Heavy imports
live inside the handlers so ``--help`` works without the geospatial stack
installed.
"""

from __future__ import annotations

import argparse
import sys

from .config import Config, ConfigError


def _print_results(results: dict) -> None:
    print("\n=== Correlation (point-to-pixel) ===")
    for bc in results["bands"]:
        print(
            f"  {bc.band:>10}: "
            f"pearson r={bc.pearson_r:+.3f} (p={bc.pearson_p:.3g}, R²={bc.r_squared:.3f})  "
            f"spearman r={bc.spearman_r:+.3f} (p={bc.spearman_p:.3g})  n={bc.n}"
        )
    if not results["bands"]:
        print("  (no band met correlation.min_valid_pairs)")
    m = results["moran"]
    print("\n=== Moran's I (methane spatial autocorrelation) ===")
    print(
        f"  I={m.I:+.3f}  E[I]={m.expected_I:+.3f}  p={m.p_value:.3g}  "
        f"n={m.n}  ({m.permutations} permutations)"
    )


def cmd_prep(config: Config):
    from . import data_prep

    gdf = data_prep.run(config)
    print(f"Phase 1 — loaded {len(gdf)} TDLAS points → PostGIS, CRS validated.")
    return gdf


def cmd_match(config: Config, gdf=None):
    from . import data_prep, spatial_match

    if gdf is None:
        gdf = data_prep.load_tdlas(config)
    gdf, _grid, _t = spatial_match.run(config, gdf)
    print("Phase 2 — sampled ortho bands at points, wrote gridded TDLAS surface.")
    return gdf


def cmd_correlate(config: Config, gdf=None):
    from . import correlation, data_prep, spatial_match

    if gdf is None:
        gdf = data_prep.load_tdlas(config)
        gdf, _g, _t = spatial_match.run(config, gdf)
    results = correlation.run(config, gdf)
    _print_results(results)
    return gdf, results


def cmd_visualize(config: Config, gdf=None, results=None):
    from . import correlation, data_prep, spatial_match, visualize

    if gdf is None or results is None:
        gdf = data_prep.load_tdlas(config)
        gdf, _g, _t = spatial_match.run(config, gdf)
        results = correlation.run(config, gdf)
    artifacts = visualize.run(config, gdf, results)
    print("Phase 4 — wrote:")
    for name, path in artifacts.items():
        print(f"  {name}: {path}")
    return artifacts


def cmd_run_all(config: Config):
    gdf = cmd_prep(config)
    gdf = cmd_match(config, gdf)
    gdf, results = cmd_correlate(config, gdf)
    cmd_visualize(config, gdf, results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sonami", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prep", "match", "correlate", "visualize", "run-all"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True, help="path to sonami.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        dispatch = {
            "prep": cmd_prep,
            "match": cmd_match,
            "correlate": cmd_correlate,
            "visualize": cmd_visualize,
            "run-all": cmd_run_all,
        }
        dispatch[args.command](config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
