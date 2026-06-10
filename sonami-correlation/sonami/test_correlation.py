"""Tests for the correlation phase (synthetic data, no real rasters)."""

from __future__ import annotations

import numpy as np
import pytest

from sonami.correlation import correlate_bands, morans_i


def test_correlate_bands_recovers_linear_relationship():
    rng = np.random.default_rng(0)
    methane = rng.normal(size=200)
    band_pos = 3 * methane + rng.normal(scale=0.1, size=200)  # strong positive
    band_noise = rng.normal(size=200)  # no relationship
    results = {
        bc.band: bc for bc in correlate_bands(methane, {"pos": band_pos, "noise": band_noise})
    }
    assert results["pos"].pearson_r > 0.95
    assert results["pos"].r_squared > 0.9
    assert results["pos"].pearson_p < 1e-10
    assert abs(results["noise"].pearson_r) < 0.2


def test_correlate_bands_skips_below_min_pairs():
    methane = np.array([1.0, 2.0, 3.0, np.nan])
    band = np.array([1.0, np.nan, 3.0, 4.0])  # only 2 complete pairs
    assert correlate_bands(methane, {"b": band}, min_valid_pairs=30) == []


def test_morans_i_positive_for_clustered_values():
    # Values increasing along a line → neighbours are similar → strong positive I.
    n = 50
    xs = np.arange(n, dtype="float64")
    ys = np.zeros(n)
    values = xs.copy()
    res = morans_i(xs, ys, values, scheme="knn", k=4, permutations=199, seed=1)
    assert res.I > 0.5
    assert res.p_value < 0.05
    assert res.expected_I == pytest.approx(-1 / (n - 1))


def test_morans_i_near_expected_for_random_field():
    rng = np.random.default_rng(7)
    xs = rng.uniform(0, 100, size=80)
    ys = rng.uniform(0, 100, size=80)
    values = rng.normal(size=80)  # no spatial structure
    res = morans_i(xs, ys, values, scheme="knn", k=8, permutations=199, seed=2)
    assert res.p_value > 0.05  # not significant
    assert abs(res.I - res.expected_I) < 0.25


def test_distance_band_requires_radius():
    xs = np.array([0.0, 1.0, 2.0])
    ys = np.zeros(3)
    with pytest.raises(ValueError, match="distance_band_m"):
        morans_i(xs, ys, xs, scheme="distance_band", permutations=9)
