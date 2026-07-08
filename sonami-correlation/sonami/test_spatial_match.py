"""Tests for the spatial-matching phase (in-memory rasters, no real files)."""

from __future__ import annotations

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.io import MemoryFile  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from sonami.spatial_match import idw, idw_grid_like, sample_raster_at_points  # noqa: E402


def _memory_raster(array: np.ndarray, transform, crs="EPSG:32617", nodata=None):
    """Yield an open in-memory single/multi-band raster."""
    bands = array.shape[0]
    mem = MemoryFile()
    dst = mem.open(
        driver="GTiff",
        height=array.shape[1],
        width=array.shape[2],
        count=bands,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
    )
    dst.write(array.astype("float32"))
    return dst


def test_sample_nearest_reads_correct_pixels():
    # 2x2 raster, 1 m pixels, origin at (0, 2) north-up.
    data = np.array([[[10.0, 20.0], [30.0, 40.0]]])  # 1 band
    transform = from_origin(0, 2, 1, 1)
    ras = _memory_raster(data, transform)
    # Pixel centres: (0.5,1.5)=10, (1.5,1.5)=20, (0.5,0.5)=30, (1.5,0.5)=40
    xs = np.array([0.5, 1.5, 0.5, 1.5])
    ys = np.array([1.5, 1.5, 0.5, 0.5])
    out = sample_raster_at_points(ras, xs, ys, ["b1"], strategy="nearest")
    np.testing.assert_allclose(out["b1"], [10, 20, 30, 40])


def test_sample_nodata_becomes_nan():
    data = np.array([[[10.0, -9999.0], [30.0, 40.0]]])
    transform = from_origin(0, 2, 1, 1)
    ras = _memory_raster(data, transform, nodata=-9999.0)
    out = sample_raster_at_points(ras, np.array([1.5]), np.array([1.5]), ["b1"])
    assert np.isnan(out["b1"][0])


def test_sample_band_count_mismatch_raises():
    data = np.array([[[1.0, 2.0], [3.0, 4.0]]])
    ras = _memory_raster(data, from_origin(0, 2, 1, 1))
    with pytest.raises(ValueError, match="band_names"):
        sample_raster_at_points(ras, np.array([0.5]), np.array([0.5]), ["a", "b"])


def test_idw_exact_at_sample_points():
    px = np.array([0.0, 10.0])
    py = np.array([0.0, 0.0])
    values = np.array([1.0, 5.0])
    out = idw(px, py, values, px, py, power=2)
    np.testing.assert_allclose(out, values)


def test_idw_midpoint_is_average_for_symmetric_pair():
    px = np.array([0.0, 10.0])
    py = np.array([0.0, 0.0])
    values = np.array([0.0, 10.0])
    out = idw(px, py, values, np.array([5.0]), np.array([0.0]), power=2)
    assert out[0] == pytest.approx(5.0)


def test_idw_grid_shape_and_bounds():
    px = np.array([0.0, 4.0, 0.0, 4.0])
    py = np.array([0.0, 0.0, 4.0, 4.0])
    values = np.array([1.0, 2.0, 3.0, 4.0])
    grid, transform = idw_grid_like(px, py, values, (0, 0, 4, 4), res=1.0)
    assert grid.shape == (4, 4)
    assert transform.a == 1.0 and transform.e == -1.0
    assert np.isfinite(grid).all()
