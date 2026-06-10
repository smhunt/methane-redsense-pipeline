"""Phase 3 — Correlation analysis.

* Pearson & Spearman correlation of methane vs each sampled band (point-to-pixel),
  with R² and p-values.
* Moran's I spatial autocorrelation of methane, with a permutation p-value.

The numeric functions take plain arrays so they are unit-testable without a
config, real rasters, or PySAL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats
from scipy.spatial import cKDTree

from .config import Config


@dataclass
class BandCorrelation:
    band: str
    n: int
    pearson_r: float
    pearson_p: float
    spearman_r: float
    spearman_p: float

    @property
    def r_squared(self) -> float:
        return self.pearson_r**2


def correlate_bands(
    methane: np.ndarray,
    band_values: dict[str, np.ndarray],
    *,
    min_valid_pairs: int = 30,
) -> list[BandCorrelation]:
    """Pearson + Spearman of ``methane`` vs each band, pairwise-complete.

    Bands with fewer than ``min_valid_pairs`` non-NaN pairs are skipped.
    """
    methane = np.asarray(methane, dtype="float64")
    results: list[BandCorrelation] = []
    for band, values in band_values.items():
        values = np.asarray(values, dtype="float64")
        mask = np.isfinite(methane) & np.isfinite(values)
        n = int(mask.sum())
        if n < min_valid_pairs:
            continue
        pr = stats.pearsonr(methane[mask], values[mask])
        sr = stats.spearmanr(methane[mask], values[mask])
        results.append(
            BandCorrelation(
                band=band,
                n=n,
                pearson_r=float(pr.statistic),
                pearson_p=float(pr.pvalue),
                spearman_r=float(sr.statistic),
                spearman_p=float(sr.pvalue),
            )
        )
    return results


def _spatial_weights(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    scheme: str = "knn",
    k: int = 8,
    distance_band_m: float | None = None,
) -> np.ndarray:
    """Row-standardised binary spatial weights matrix W (n x n), no self-links."""
    pts = np.column_stack([xs, ys])
    n = len(pts)
    tree = cKDTree(pts)
    w = np.zeros((n, n), dtype="float64")

    if scheme == "knn":
        k = min(k, n - 1)
        _, idx = tree.query(pts, k=k + 1)  # +1: first neighbour is self
        for i in range(n):
            for j in idx[i, 1:]:
                w[i, j] = 1.0
    elif scheme == "distance_band":
        if distance_band_m is None:
            raise ValueError("distance_band weights require correlation.moran.distance_band_m.")
        neighbours = tree.query_ball_point(pts, r=distance_band_m)
        for i, nbrs in enumerate(neighbours):
            for j in nbrs:
                if j != i:
                    w[i, j] = 1.0
    else:
        raise ValueError(f"Unknown weights scheme {scheme!r} (use knn|distance_band).")

    rowsum = w.sum(axis=1, keepdims=True)
    rowsum[rowsum == 0] = 1.0  # isolates contribute nothing rather than dividing by 0
    return w / rowsum


@dataclass
class MoranResult:
    I: float
    expected_I: float
    p_value: float
    n: int
    permutations: int
    permuted_I: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))


def _morans_i(values: np.ndarray, w: np.ndarray) -> float:
    z = values - values.mean()
    s0 = w.sum()
    num = z @ (w @ z)
    den = z @ z
    if den == 0 or s0 == 0:
        return float("nan")
    return float((len(values) / s0) * (num / den))


def morans_i(
    xs: np.ndarray,
    ys: np.ndarray,
    values: np.ndarray,
    *,
    scheme: str = "knn",
    k: int = 8,
    distance_band_m: float | None = None,
    permutations: int = 999,
    seed: int | None = None,
) -> MoranResult:
    """Global Moran's I with a conditional-permutation p-value (two-sided)."""
    xs = np.asarray(xs, dtype="float64")
    ys = np.asarray(ys, dtype="float64")
    values = np.asarray(values, dtype="float64")
    mask = np.isfinite(values)
    xs, ys, values = xs[mask], ys[mask], values[mask]
    n = len(values)

    w = _spatial_weights(xs, ys, scheme=scheme, k=k, distance_band_m=distance_band_m)
    observed = _morans_i(values, w)
    expected = -1.0 / (n - 1)

    rng = np.random.default_rng(seed)
    perm = np.empty(permutations, dtype="float64")
    for p in range(permutations):
        perm[p] = _morans_i(rng.permutation(values), w)

    # Two-sided pseudo p-value (add-one smoothing).
    extreme = int(np.sum(np.abs(perm - expected) >= abs(observed - expected)))
    p_value = (extreme + 1) / (permutations + 1)
    return MoranResult(
        I=observed,
        expected_I=expected,
        p_value=p_value,
        n=n,
        permutations=permutations,
        permuted_I=perm,
    )


def run(config: Config, gdf) -> dict:
    """Phase 3: correlate sampled bands and compute Moran's I on methane."""
    band_names = config.require("data.pix4d.bands")
    min_pairs = int(config.get("correlation.min_valid_pairs", 30))
    band_values = {b: gdf[f"band_{b}"].to_numpy(dtype="float64") for b in band_names}
    methane = gdf["methane_ppm"].to_numpy(dtype="float64")

    band_corr = correlate_bands(methane, band_values, min_valid_pairs=min_pairs)

    mcfg = config.get("correlation.moran", {}) or {}
    moran = morans_i(
        gdf.geometry.x.to_numpy(),
        gdf.geometry.y.to_numpy(),
        methane,
        scheme=mcfg.get("weights", "knn"),
        k=int(mcfg.get("k", 8)),
        distance_band_m=mcfg.get("distance_band_m"),
        permutations=int(mcfg.get("permutations", 999)),
    )
    return {"bands": band_corr, "moran": moran}
