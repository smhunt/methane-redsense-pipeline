"""Tests for src.preprocess.exif_audit.

Validation logic is pure (operates on metadata dicts), so it's tested
without invoking exiftool. The end-to-end exiftool integration would
need real MicaSense captures and is left for the field.
"""

from __future__ import annotations

from pathlib import Path

from src.preprocess import exif_audit


def test_band_from_filename_valid():
    assert exif_audit.band_from_filename(Path("IMG_0000_1.tif")) == 1
    assert exif_audit.band_from_filename(Path("IMG_9999_5.tif")) == 5
    assert exif_audit.band_from_filename(Path("/x/IMG_0042_3.tif")) == 3


def test_band_from_filename_invalid():
    assert exif_audit.band_from_filename(Path("IMG_no_band.tif")) is None
    assert exif_audit.band_from_filename(Path("random.tif")) is None
    assert exif_audit.band_from_filename(Path("IMG_0000.tif")) is None


def test_check_tags_all_present():
    meta = {
        "EXIF:RadiometricCalibration": "1.0 2.0 3.0",
        "XMP:VignettingPolynomial": "0 0 0 0 0 0",
        "XMP:VignettingCenter": "1024 768",
        "EXIF:GPSLatitude": 53.5,
        "EXIF:GPSLongitude": -113.5,
        "EXIF:GPSAltitude": 700,
        "XMP:BandName": "Red",
        "XMP:Irradiance": 1200.0,
    }
    missing_req, missing_opt = exif_audit.check_tags(meta)
    assert missing_req == ()
    assert missing_opt == ()


def test_check_tags_missing_required():
    meta = {"EXIF:RadiometricCalibration": "1.0 2.0 3.0"}
    missing_req, missing_opt = exif_audit.check_tags(meta)
    assert "VignettingPolynomial" in missing_req
    assert "GPSLatitude" in missing_req
    assert "BandName" in missing_req
    assert "RadiometricCalibration" not in missing_req
    assert "Irradiance" in missing_opt


def test_check_tags_group_insensitive():
    """Bare and group-prefixed tag names both satisfy."""
    meta = {
        "RadiometricCalibration": "1.0 2.0 3.0",
        "XMP-Camera:VignettingPolynomial": "...",
        "EXIF:GPSLatitude": 53.5,
    }
    missing_req, _ = exif_audit.check_tags(meta)
    assert "RadiometricCalibration" not in missing_req
    assert "VignettingPolynomial" not in missing_req
    assert "GPSLatitude" not in missing_req


def test_find_captures(tmp_path: Path):
    capture_dir = tmp_path / "0000SET" / "000"
    capture_dir.mkdir(parents=True)
    (capture_dir / "IMG_0000_1.tif").touch()
    (capture_dir / "IMG_0000_2.tif").touch()
    (capture_dir / "IMG_0001_1.tif").touch()
    (capture_dir / "notes.txt").touch()
    (capture_dir / "random.tif").touch()  # no IMG_ prefix → ignored

    captures = exif_audit.find_captures(tmp_path)
    assert [p.name for p in captures] == [
        "IMG_0000_1.tif",
        "IMG_0000_2.tif",
        "IMG_0001_1.tif",
    ]


def test_find_captures_empty(tmp_path: Path):
    assert exif_audit.find_captures(tmp_path) == []


def test_format_report_no_files(tmp_path: Path):
    audit = exif_audit.FlightAudit(flight_dir=tmp_path, files=())
    report = exif_audit.format_report(audit)
    assert "Files found: 0" in report
    assert "No IMG_*.tif" in report


def test_format_report_all_pass(tmp_path: Path):
    audit = exif_audit.FlightAudit(
        flight_dir=tmp_path,
        files=(
            exif_audit.FileAudit(
                path=tmp_path / "IMG_0000_1.tif",
                band=1,
                missing_required=(),
                missing_optional=(),
            ),
        ),
    )
    report = exif_audit.format_report(audit)
    assert "Files passing: 1 / 1" in report
    assert "Missing REQUIRED" not in report
    assert "Missing OPTIONAL" not in report


def test_format_report_with_failures(tmp_path: Path):
    audit = exif_audit.FlightAudit(
        flight_dir=tmp_path,
        files=(
            exif_audit.FileAudit(
                path=tmp_path / "IMG_0000_1.tif",
                band=1,
                missing_required=("RadiometricCalibration",),
                missing_optional=("Irradiance",),
            ),
            exif_audit.FileAudit(
                path=tmp_path / "IMG_0001_2.tif",
                band=2,
                missing_required=(),
                missing_optional=(),
            ),
        ),
    )
    report = exif_audit.format_report(audit)
    assert "Files passing: 1 / 2" in report
    assert "RadiometricCalibration" in report
    assert "DLS2 Irradiance missing" in report
    assert "First 1 failing captures" in report
    assert "IMG_0000_1.tif" in report


def test_main_not_a_directory(tmp_path: Path, capsys):
    bogus = tmp_path / "does_not_exist"
    rc = exif_audit.main([str(bogus)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not a directory" in err


def test_main_empty_directory_exits_nonzero(tmp_path: Path, capsys):
    rc = exif_audit.main([str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "No IMG_*.tif" in out
