"""Tests for src.calibration.calibrate.

Pure logic only — CSV parsing, capture discovery + grouping, band-name
mapping, the float32 TIF writer (roundtrip), band stats. The micasense
calibration math and the exiftool tag copy are field-tested on real
captures; we don't have synthetic MicaSense fixtures with valid
radiometric polynomials.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio

from src.calibration import calibrate, run

# ─── Panel CSV ───


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_parse_panel_csv_valid(tmp_path: Path):
    csv_path = _write(
        tmp_path / "panel.csv",
        "band,reflectance\nblue,0.52\ngreen,0.519\nred,0.518\nnir,0.516\nrededge,0.517\n",
    )
    result = calibrate.parse_panel_csv(csv_path)
    assert result == {
        "blue": 0.52,
        "green": 0.519,
        "red": 0.518,
        "nir": 0.516,
        "rededge": 0.517,
    }


def test_parse_panel_csv_case_insensitive(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "band,reflectance\nBLUE,0.52\nGreen,0.5\n")
    result = calibrate.parse_panel_csv(csv_path)
    assert "blue" in result and "green" in result


def test_parse_panel_csv_percent_rejected(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "band,reflectance\nblue,52\n")
    with pytest.raises(calibrate.PanelCSVError, match="fraction"):
        calibrate.parse_panel_csv(csv_path)


def test_parse_panel_csv_negative_rejected(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "band,reflectance\nblue,-0.1\n")
    with pytest.raises(calibrate.PanelCSVError):
        calibrate.parse_panel_csv(csv_path)


def test_parse_panel_csv_zero_rejected(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "band,reflectance\nblue,0\n")
    with pytest.raises(calibrate.PanelCSVError):
        calibrate.parse_panel_csv(csv_path)


def test_parse_panel_csv_missing_file(tmp_path: Path):
    with pytest.raises(calibrate.PanelCSVError, match="not found"):
        calibrate.parse_panel_csv(tmp_path / "nope.csv")


def test_parse_panel_csv_bad_header(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "name,value\nblue,0.5\n")
    with pytest.raises(calibrate.PanelCSVError, match="header"):
        calibrate.parse_panel_csv(csv_path)


def test_parse_panel_csv_empty(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "band,reflectance\n")
    with pytest.raises(calibrate.PanelCSVError, match="no data"):
        calibrate.parse_panel_csv(csv_path)


def test_parse_panel_csv_non_numeric(tmp_path: Path):
    csv_path = _write(tmp_path / "panel.csv", "band,reflectance\nblue,abc\n")
    with pytest.raises(calibrate.PanelCSVError, match="Non-numeric"):
        calibrate.parse_panel_csv(csv_path)


# ─── Panel CSV → ordered lookup ───


def test_panel_csv_lookup_order():
    data = {"blue": 0.52, "green": 0.51, "red": 0.50, "nir": 0.49, "rededge": 0.48}
    result = calibrate.panel_csv_lookup(data, ["Red", "Blue", "NIR", "Green", "Red edge"])
    assert result == [0.50, 0.52, 0.49, 0.51, 0.48]


def test_panel_csv_lookup_normalizes_red_edge_space():
    """micasense returns 'Red edge' with a space; CSV uses 'rededge'."""
    data = {"rededge": 0.48}
    assert calibrate.panel_csv_lookup(data, ["Red edge"]) == [0.48]


def test_panel_csv_lookup_missing_raises():
    data = {"blue": 0.5}
    with pytest.raises(calibrate.PanelCSVError, match="missing"):
        calibrate.panel_csv_lookup(data, ["Red"])


# ─── Capture discovery + grouping ───


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_group_by_capture_basic(tmp_path: Path):
    p1 = _touch(tmp_path / "IMG_0001_1.tif")
    p2 = _touch(tmp_path / "IMG_0001_2.tif")
    p3 = _touch(tmp_path / "IMG_0002_1.tif")
    groups = calibrate.group_by_capture([p1, p3, p2])
    assert len(groups) == 2
    assert groups[0] == [p1, p2]
    assert groups[1] == [p3]


def test_group_by_capture_separates_set_dirs(tmp_path: Path):
    """MicaSense restarts the capture counter per SET — same IMG_xxxx in
    different SET dirs must NOT collide."""
    p_set0 = _touch(tmp_path / "0000SET" / "000" / "IMG_0001_1.tif")
    p_set1 = _touch(tmp_path / "0000SET" / "001" / "IMG_0001_1.tif")
    groups = calibrate.group_by_capture([p_set0, p_set1])
    assert len(groups) == 2


def test_group_by_capture_skips_bad_names(tmp_path: Path):
    p_ok = _touch(tmp_path / "IMG_0001_1.tif")
    _touch(tmp_path / "random.tif")
    _touch(tmp_path / "IMG_nope.tif")
    groups = calibrate.group_by_capture(tmp_path.iterdir())
    assert groups == [[p_ok]]


def test_find_panel_captures(tmp_path: Path):
    _touch(tmp_path / "panel" / "IMG_0000_1.tif")
    _touch(tmp_path / "panel" / "IMG_0000_2.tif")
    _touch(tmp_path / "0000SET" / "000" / "IMG_0001_1.tif")
    groups = calibrate.find_panel_captures(tmp_path)
    assert len(groups) == 1
    assert all("panel" in str(p) for p in groups[0])


def test_find_panel_captures_no_panel_dir(tmp_path: Path):
    assert calibrate.find_panel_captures(tmp_path) == []


def test_find_working_captures_excludes_panel(tmp_path: Path):
    _touch(tmp_path / "panel" / "IMG_0000_1.tif")
    _touch(tmp_path / "0000SET" / "000" / "IMG_0001_1.tif")
    _touch(tmp_path / "0000SET" / "000" / "IMG_0001_2.tif")
    groups = calibrate.find_working_captures(tmp_path)
    assert len(groups) == 1
    assert all("panel" not in str(p) for p in groups[0])


def test_find_working_captures_no_panel_dir(tmp_path: Path):
    """find_working_captures shouldn't crash when there's no panel/ subdir."""
    _touch(tmp_path / "IMG_0001_1.tif")
    groups = calibrate.find_working_captures(tmp_path)
    assert len(groups) == 1


# ─── Reflectance TIF writer ───


def test_write_reflectance_tif_roundtrip(tmp_path: Path):
    arr = np.array([[0.1, 0.5, 0.9], [0.0, 1.0, 0.7]], dtype=np.float32)
    out = tmp_path / "deep" / "test.tif"
    calibrate.write_reflectance_tif(arr, out)
    assert out.is_file()
    with rasterio.open(out) as ds:
        assert ds.count == 1
        assert ds.dtypes[0] == "float32"
        assert (ds.width, ds.height) == (3, 2)
        np.testing.assert_array_almost_equal(ds.read(1), arr)


def test_write_reflectance_tif_preserves_out_of_range(tmp_path: Path):
    """No silent clipping — calibration errors must stay visible."""
    arr = np.array([[-0.1, 1.5, 0.5]], dtype=np.float32)
    out = tmp_path / "test.tif"
    calibrate.write_reflectance_tif(arr, out)
    with rasterio.open(out) as ds:
        read = ds.read(1)
    np.testing.assert_array_almost_equal(read, arr)


def test_write_reflectance_tif_rejects_non_2d(tmp_path: Path):
    arr = np.zeros((2, 3, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="2-D"):
        calibrate.write_reflectance_tif(arr, tmp_path / "never.tif")


# ─── Band stats ───


def test_band_stats_basic():
    arr = np.array([[0.1, 0.5], [0.9, 0.7]], dtype=np.float32)
    s = calibrate._band_stats(arr, "Red")
    assert s.band_name == "Red"
    assert s.mean == pytest.approx(0.55)
    assert s.max == pytest.approx(0.9)
    assert s.warning is None


def test_band_stats_warning_over_threshold():
    arr = np.array([[0.1, 1.5]], dtype=np.float32)
    s = calibrate._band_stats(arr, "Red")
    assert s.warning is not None and "1.500" in s.warning


def test_band_stats_below_threshold_no_warn():
    """Just under 1.2 should NOT warn (only >1.2 per CLAUDE.md).

    Note: 1.2 as float32 rounds to ~1.20000005, which is > 1.2 and *does*
    warn — that's correct float behavior, so we test 1.19 instead.
    """
    arr = np.array([[1.19]], dtype=np.float32)
    s = calibrate._band_stats(arr, "Red")
    assert s.warning is None


def test_band_stats_ignores_nan():
    arr = np.array([[np.nan, 0.5, 0.5]], dtype=np.float32)
    s = calibrate._band_stats(arr, "Red")
    assert s.mean == pytest.approx(0.5)


def test_band_stats_all_nan():
    arr = np.array([[np.nan, np.nan]], dtype=np.float32)
    s = calibrate._band_stats(arr, "Red")
    assert np.isnan(s.mean) and np.isnan(s.max)


# ─── Output dir redirection (radiance-only) ───


def test_radiance_only_dir_naming():
    p = Path("/x/outputs/calibrated/flight_X")
    assert calibrate._radiance_only_dir(p) == Path("/x/outputs/calibrated/flight_X_RADIANCE_ONLY")


# ─── CLI error paths ───


def test_main_missing_input_dir(tmp_path: Path, capsys):
    rc = run.main(
        [
            "--input",
            str(tmp_path / "nope"),
            "--panel",
            str(tmp_path / "panel.csv"),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert rc == calibrate.EXIT_BAD_ARGS
    assert "not a directory" in capsys.readouterr().err


def test_main_panel_csv_missing(tmp_path: Path, capsys):
    (tmp_path / "panel").mkdir()
    rc = run.main(
        [
            "--input",
            str(tmp_path),
            "--panel",
            str(tmp_path / "nope.csv"),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert rc == calibrate.EXIT_BAD_ARGS
    assert "not found" in capsys.readouterr().err


def test_main_no_panel_captures(tmp_path: Path, capsys):
    _write(tmp_path / "panel.csv", "band,reflectance\nblue,0.5\n")
    rc = run.main(
        [
            "--input",
            str(tmp_path),
            "--panel",
            str(tmp_path / "panel.csv"),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert rc == calibrate.EXIT_BAD_ARGS
    assert "No panel captures" in capsys.readouterr().err
