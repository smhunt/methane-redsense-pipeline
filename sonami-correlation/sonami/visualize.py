"""Phase 4 — Visualization & export.

* Folium map of TDLAS points over the ortho footprint, coloured by methane and
  by local agreement with the best-correlated band ("high-correlation zones").
* GeoJSON export for stakeholder review.
* Statistical scatter plots (methane vs band).

Folium needs WGS84 lon/lat, so the (projected) analysis-CRS GeoDataFrame is
reprojected to EPSG:4326 for mapping only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import Config


def local_agreement(methane: np.ndarray, band: np.ndarray) -> np.ndarray:
    """Per-point bivariate association z(methane)·z(band).

    A LISA-style local indicator: large positive values are points where both
    methane and the band are jointly high (or jointly low) — the "high
    agreement" zones to highlight; large negative values are disagreements.
    NaN where either input is NaN.
    """
    methane = np.asarray(methane, dtype="float64")
    band = np.asarray(band, dtype="float64")
    mask = np.isfinite(methane) & np.isfinite(band)
    zm = np.full_like(methane, np.nan)
    zb = np.full_like(band, np.nan)
    if mask.sum() > 1:
        zm[mask] = (methane[mask] - methane[mask].mean()) / methane[mask].std(ddof=0)
        zb[mask] = (band[mask] - band[mask].mean()) / band[mask].std(ddof=0)
    return zm * zb


def export_geojson(gdf, path: str | Path) -> Path:
    """Write the GeoDataFrame to GeoJSON (reprojected to WGS84)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
    return path


def correlation_map(gdf, value_col: str, out_html: str | Path, *, agreement_col: str | None = None):
    """Build a folium map: TDLAS points sized/coloured by methane.

    If ``agreement_col`` is given, points are coloured by local agreement
    (high-correlation zones) instead of raw value.
    """
    import folium

    wgs = gdf.to_crs("EPSG:4326")
    cx = float(wgs.geometry.y.mean())
    cy = float(wgs.geometry.x.mean())
    fmap = folium.Map(location=[cx, cy], zoom_start=18, tiles="OpenStreetMap")

    color_col = agreement_col or value_col
    series = wgs[color_col].to_numpy(dtype="float64")
    finite = series[np.isfinite(series)]
    lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
    span = (hi - lo) or 1.0

    for _, row in wgs.iterrows():
        val = row[color_col]
        if not np.isfinite(val):
            continue
        t = (val - lo) / span
        color = f"#{int(255 * t):02x}00{int(255 * (1 - t)):02x}"  # blue→red
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=5,
            color=color,
            fill=True,
            fill_opacity=0.8,
            popup=f"{value_col}={row[value_col]:.3g}",
        ).add_to(fmap)

    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(out_html))
    return out_html


def scatter_plot(methane: np.ndarray, band: np.ndarray, band_name: str, out_png: str | Path):
    """Scatter of methane vs a band, with an OLS fit line."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methane = np.asarray(methane, dtype="float64")
    band = np.asarray(band, dtype="float64")
    mask = np.isfinite(methane) & np.isfinite(band)
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(band[mask], methane[mask], s=10, alpha=0.6)
    if mask.sum() > 1:
        slope, intercept = np.polyfit(band[mask], methane[mask], 1)
        xs = np.linspace(band[mask].min(), band[mask].max(), 100)
        ax.plot(xs, slope * xs + intercept, color="crimson", lw=1.5)
    ax.set_xlabel(band_name)
    ax.set_ylabel("methane_ppm")
    ax.set_title(f"methane vs {band_name}")
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def run(config: Config, gdf, results: dict) -> dict:
    """Phase 4: pick the best band, write map + GeoJSON + scatter."""
    out_dir = config.output_dir
    bands = results.get("bands", [])
    best = max(bands, key=lambda b: abs(b.pearson_r), default=None)

    methane = gdf["methane_ppm"].to_numpy(dtype="float64")
    artifacts: dict[str, Path] = {}

    if best is not None:
        band_vals = gdf[f"band_{best.band}"].to_numpy(dtype="float64")
        gdf["agreement"] = local_agreement(methane, band_vals)
        artifacts["map"] = correlation_map(
            gdf,
            "methane_ppm",
            out_dir / config.get("output.heatmap_html", "correlation_map.html"),
            agreement_col="agreement",
        )
        artifacts["scatter"] = scatter_plot(
            methane, band_vals, best.band, out_dir / f"scatter_{best.band}.png"
        )
    else:
        artifacts["map"] = correlation_map(
            gdf, "methane_ppm", out_dir / config.get("output.heatmap_html", "correlation_map.html")
        )

    artifacts["geojson"] = export_geojson(
        gdf, out_dir / config.get("output.geojson", "correlation_points.geojson")
    )
    return {k: str(v) for k, v in artifacts.items()}
