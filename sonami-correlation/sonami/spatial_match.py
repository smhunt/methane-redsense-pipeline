"""Phase 2 — Spatial matching.

* Sample Pix4D raster band values at each TDLAS point.
* Build a gridded TDLAS surface by interpolation (IDW built-in, kriging optional).
* Align the gridded surface to the orthomosaic's grid.

The numeric core (``sample_raster_at_points``, ``idw``) takes plain arrays so it
is unit-testable without a config or real files.
"""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy.spatial import cKDTree

from .config import Config
from .data_prep import open_orthomosaic


def sample_raster_at_points(
    raster: rasterio.io.DatasetReader,
    xs: np.ndarray,
    ys: np.ndarray,
    band_names: list[str],
    *,
    strategy: str = "nearest",
) -> dict[str, np.ndarray]:
    """Return ``{band_name: values}`` sampled at the (xs, ys) coordinates.

    Coordinates must already be in the raster's CRS. ``nearest`` reads the pixel
    a point falls in; ``bilinear`` interpolates the 2x2 neighbourhood. Off-raster
    or nodata samples come back as NaN.
    """
    if len(band_names) != raster.count:
        raise ValueError(
            f"band_names has {len(band_names)} entries but raster has {raster.count} bands."
        )
    coords = list(zip(xs, ys, strict=True))
    if strategy == "nearest":
        sampled = np.array(list(raster.sample(coords)), dtype="float64")
    elif strategy == "bilinear":
        sampled = _sample_bilinear(raster, xs, ys)
    else:
        raise ValueError(f"Unknown sample strategy {strategy!r} (use nearest|bilinear).")

    out: dict[str, np.ndarray] = {}
    nodata = raster.nodatavals
    for i, name in enumerate(band_names):
        col = sampled[:, i].astype("float64")
        nd = nodata[i] if i < len(nodata) else None
        if nd is not None:
            col[col == nd] = np.nan
        out[name] = col
    return out


def _sample_bilinear(raster, xs, ys) -> np.ndarray:
    """Bilinear sample of every band at fractional pixel positions."""
    inv = ~raster.transform
    out = np.full((len(xs), raster.count), np.nan, dtype="float64")
    data = raster.read(masked=True).astype("float64").filled(np.nan)  # (bands, H, W)
    _, height, width = data.shape
    for n, (x, y) in enumerate(zip(xs, ys, strict=True)):
        fcol, frow = inv * (x, y)
        c0, r0 = int(np.floor(fcol - 0.5)), int(np.floor(frow - 0.5))
        dc, dr = (fcol - 0.5) - c0, (frow - 0.5) - r0
        if not (0 <= c0 < width - 1 and 0 <= r0 < height - 1):
            continue
        w = np.array([(1 - dc) * (1 - dr), dc * (1 - dr), (1 - dc) * dr, dc * dr])
        block = data[:, r0 : r0 + 2, c0 : c0 + 2].reshape(raster.count, 4)
        out[n] = block @ w
    return out


def idw(
    px: np.ndarray,
    py: np.ndarray,
    values: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    *,
    power: float = 2.0,
    k: int | None = None,
) -> np.ndarray:
    """Inverse-distance-weighted interpolation onto grid coordinates.

    ``px, py, values`` are the known points; ``gx, gy`` are flattened grid
    coordinates. With ``k`` set, only the k nearest samples weigh each cell
    (faster on large grids). A grid point coincident with a sample takes that
    sample's value exactly.
    """
    pts = np.column_stack([px, py])
    grid = np.column_stack([gx, gy])
    tree = cKDTree(pts)
    if k is None:
        k = len(pts)
    k = min(k, len(pts))
    dist, idx = tree.query(grid, k=k)
    if k == 1:
        dist = dist[:, None]
        idx = idx[:, None]

    out = np.empty(len(grid), dtype="float64")
    exact = dist[:, 0] == 0
    out[exact] = values[idx[exact, 0]]

    rem = ~exact
    w = 1.0 / np.power(dist[rem], power)
    out[rem] = np.sum(w * values[idx[rem]], axis=1) / np.sum(w, axis=1)
    return out


def idw_grid_like(
    px: np.ndarray,
    py: np.ndarray,
    values: np.ndarray,
    bounds: tuple[float, float, float, float],
    res: float,
    *,
    power: float = 2.0,
    k: int | None = None,
) -> tuple[np.ndarray, rasterio.Affine]:
    """IDW onto a regular grid covering ``bounds`` (left, bottom, right, top).

    Returns ``(array, transform)`` with array shape (rows, cols), north-up.
    """
    left, bottom, right, top = bounds
    cols = int(np.ceil((right - left) / res))
    rows = int(np.ceil((top - bottom) / res))
    transform = from_origin(left, top, res, res)
    # Cell centres.
    cx = left + (np.arange(cols) + 0.5) * res
    cy = top - (np.arange(rows) + 0.5) * res
    gx, gy = np.meshgrid(cx, cy)
    flat = idw(px, py, values, gx.ravel(), gy.ravel(), power=power, k=k)
    return flat.reshape(rows, cols), transform


def write_raster(array: np.ndarray, transform, crs, path) -> None:
    """Write a single-band float32 GeoTIFF."""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=np.nan,
    ) as dst:
        dst.write(array.astype("float32"), 1)


def run(config: Config, gdf):
    """Phase 2: sample ortho bands at points and build the gridded TDLAS surface.

    Returns ``(gdf_with_bands, grid_array, grid_transform)``. ``gdf`` must
    already be in the analysis CRS (output of Phase 1).
    """
    band_names = config.require("data.pix4d.bands")
    strategy = config.get("spatial_match.sample_strategy", "nearest")
    res = float(config.require("spatial_match.grid_res_m"))
    power = float(config.get("spatial_match.idw_power", 2))
    interp = config.get("spatial_match.interpolation", "idw")

    xs = gdf.geometry.x.to_numpy()
    ys = gdf.geometry.y.to_numpy()

    with open_orthomosaic(config) as ras:
        samples = sample_raster_at_points(ras, xs, ys, band_names, strategy=strategy)
        analysis_crs = ras.crs
    for name, col in samples.items():
        gdf[f"band_{name}"] = col

    methane = gdf["methane_ppm"].to_numpy(dtype="float64")
    if interp == "idw":
        grid, transform = idw_grid_like(xs, ys, methane, tuple(gdf.total_bounds), res, power=power)
    elif interp == "kriging":
        grid, transform = _kriging_grid_like(xs, ys, methane, tuple(gdf.total_bounds), res)
    else:
        raise ValueError(f"Unknown interpolation {interp!r} (use idw|kriging).")

    out_dir = config.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_raster(grid, transform, analysis_crs, out_dir / "tdlas_surface.tif")
    return gdf, grid, transform


def _kriging_grid_like(px, py, values, bounds, res):
    """Ordinary kriging via pykrige (optional ``[kriging]`` extra)."""
    try:
        from pykrige.ok import OrdinaryKriging
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ImportError(
            "kriging interpolation requires the optional dependency: "
            "pip install 'sonami-correlation[kriging]'"
        ) from exc
    left, bottom, right, top = bounds
    cols = int(np.ceil((right - left) / res))
    rows = int(np.ceil((top - bottom) / res))
    gridx = left + (np.arange(cols) + 0.5) * res
    gridy = bottom + (np.arange(rows) + 0.5) * res
    ok = OrdinaryKriging(px, py, values, variogram_model="spherical")
    z, _ = ok.execute("grid", gridx, gridy)
    transform = from_origin(left, top, res, res)
    return np.flipud(np.asarray(z)), transform
