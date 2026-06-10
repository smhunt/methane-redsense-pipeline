"""Tests for config loading/validation (no geospatial deps required)."""

from __future__ import annotations

import textwrap

import pytest

from sonami.config import Config, ConfigError


def _write(tmp_path, body: str):
    p = tmp_path / "sonami.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_require_raises_on_null(tmp_path):
    path = _write(
        tmp_path,
        """
        project:
          analysis_crs: null
        data:
          root: "../data"
        """,
    )
    cfg = Config.load(path)
    assert cfg.get("data.root") == "../data"
    with pytest.raises(ConfigError, match="project.analysis_crs"):
        cfg.require("project.analysis_crs")


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        Config.load("/no/such/sonami.yaml")


def test_data_path_resolves_relative_to_root(tmp_path):
    (tmp_path / "data").mkdir()
    path = _write(
        tmp_path,
        """
        data:
          root: "./data"
          tdlas:
            csv: "SONAMI/tdlas/x.csv"
        """,
    )
    cfg = Config.load(path)
    assert cfg.data_root == (tmp_path / "data").resolve()
    assert cfg.data_path("data.tdlas.csv") == (tmp_path / "data/SONAMI/tdlas/x.csv").resolve()


def test_pg_url_requires_password_env(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        """
        postgis:
          user: u
          host: h
          dbname: d
          password_env: SONAMI_TEST_PW
        """,
    )
    cfg = Config.load(path)
    monkeypatch.delenv("SONAMI_TEST_PW", raising=False)
    with pytest.raises(ConfigError, match="password"):
        cfg.pg_url()
    monkeypatch.setenv("SONAMI_TEST_PW", "secret")
    assert cfg.pg_url() == "postgresql+psycopg2://u:secret@h:5432/d"
