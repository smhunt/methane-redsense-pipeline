"""Phase 1 — Data preparation.

* Load the TDLAS CSV into a GeoDataFrame and reproject to the analysis CRS.
* Validate coordinate-system consistency between TDLAS points and the Pix4D raster.
* Load the TDLAS points into PostGIS (geometry-indexed).
* Open the Pix4D orthomosaic for downstream sampling.

All paths/CRS come from :class:`~sonami.config.Config`; nothing is hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import rasterio
from sqlalchemy import create_engine, text

from .config import Config, ConfigError

CANONICAL_COLUMNS = ["timestamp", "lat", "lon", "methane_ppm", "altitude"]


def load_tdlas(config: Config) -> gpd.GeoDataFrame:
    """Read the TDLAS CSV → GeoDataFrame in the analysis CRS.

    Column names are remapped from the CSV's actual headers (``data.tdlas.columns``)
    to the canonical names, point geometry is built from lon/lat in the source
    CRS (``data.tdlas.crs``), then reprojected to ``project.analysis_crs``.
    """
    csv_path = config.data_path("data.tdlas.csv")
    src_crs = config.require("data.tdlas.crs")
    analysis_crs = config.require("project.analysis_crs")
    colmap = config.get("data.tdlas.columns", {}) or {}

    df = pd.read_csv(csv_path)

    # Remap actual -> canonical (rename keys are actual column names).
    rename = {actual: canon for canon, actual in colmap.items()}
    df = df.rename(columns=rename)

    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    if missing:
        raise ConfigError(
            f"TDLAS CSV {csv_path.name} is missing columns {missing} after applying "
            f"data.tdlas.columns mapping. Present columns: {list(df.columns)}."
        )

    expected = config.get("data.tdlas.sample_count")
    if expected is not None and len(df) != expected:
        raise ConfigError(
            f"TDLAS row count {len(df)} != configured data.tdlas.sample_count {expected}. "
            f"Resolve the mismatch (or clear the field) before proceeding."
        )

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs=src_crs,
    )
    return gdf.to_crs(analysis_crs)


def open_orthomosaic(config: Config) -> rasterio.io.DatasetReader:
    """Open the Pix4D orthomosaic. Caller is responsible for closing it."""
    path = config.data_path("data.pix4d.orthomosaic")
    return rasterio.open(path)


def validate_crs_consistency(config: Config, gdf: gpd.GeoDataFrame) -> str:
    """Assert TDLAS points, the raster, and the analysis CRS all agree.

    Returns the common analysis CRS string. Raises if the raster's CRS differs
    from the analysis CRS (it must be reprojected first — we don't do it
    silently, since reprojecting a large ortho is a deliberate, costly step).
    """
    analysis_crs = config.require("project.analysis_crs")
    if gdf.crs is None or gdf.crs.to_string() != str(analysis_crs):
        raise ConfigError(
            f"TDLAS GeoDataFrame CRS {gdf.crs} != analysis CRS {analysis_crs}. "
            f"Call load_tdlas() which reprojects, before validating."
        )
    with open_orthomosaic(config) as ras:
        if ras.crs is None:
            raise ConfigError(
                "Pix4D orthomosaic has no CRS. Set one in Pix4D export or with gdal_edit."
            )
        if str(ras.crs) != str(analysis_crs):
            raise ConfigError(
                f"Orthomosaic CRS {ras.crs} != analysis CRS {analysis_crs}. "
                f"Reproject the ortho (e.g. gdalwarp -t_srs {analysis_crs}) and update config."
            )
    return str(analysis_crs)


@dataclass
class LoadResult:
    table: str
    rows: int


def tdlas_to_postgis(
    config: Config, gdf: gpd.GeoDataFrame, *, if_exists: str = "replace"
) -> LoadResult:
    """Write the TDLAS points to PostGIS and ensure a spatial (GiST) index.

    Requires PostGIS to be installed in the target database. The geometry column
    is named ``geom`` so the GiST index DDL below matches.
    """
    table = config.get("postgis.tdlas_table", "tdlas_points")
    engine = create_engine(config.pg_url())
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        gdf.to_postgis(table, engine, if_exists=if_exists, index=False)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS "{table}_geom_idx" '
                    f'ON "{table}" USING GIST (geometry)'
                )
            )
    finally:
        engine.dispose()
    return LoadResult(table=table, rows=len(gdf))


def run(config: Config) -> gpd.GeoDataFrame:
    """Execute Phase 1 end to end; return the loaded TDLAS GeoDataFrame."""
    gdf = load_tdlas(config)
    validate_crs_consistency(config, gdf)
    tdlas_to_postgis(config, gdf)
    return gdf
