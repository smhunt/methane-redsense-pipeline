"""Configuration loading and validation.

No dataset-specific values are baked in here. Paths, CRS, GSD, and DB
credentials all come from a YAML file (see ``config/sonami.example.yaml``) plus
environment variables for secrets. A field left as ``null`` in the YAML is
treated as "not provided yet" and raises :class:`ConfigError` the moment a
phase actually needs it — so the pipeline fails loudly with the offending key
name rather than silently inventing a value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """A required config value is missing or still a placeholder."""


@dataclass
class Config:
    """Thin wrapper over the parsed YAML with dotted-path access + validation."""

    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Config:
        path = Path(path)
        if not path.exists():
            raise ConfigError(
                f"Config file not found: {path}. "
                f"Copy config/sonami.example.yaml to config/sonami.yaml and fill it in."
            )
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(raw=raw, path=path)

    # -- access -------------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        """Return ``raw[a][b][c]`` for ``"a.b.c"``, or ``default`` if absent."""
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        """Like :meth:`get`, but raise if the value is missing or ``None``."""
        value = self.get(dotted, None)
        if value is None:
            raise ConfigError(
                f"Required config value '{dotted}' is not set. "
                f"Fill it in {self.path.name} (see config/sonami.example.yaml)."
            )
        return value

    # -- derived paths ------------------------------------------------------
    @property
    def data_root(self) -> Path:
        """Absolute path to the shared data root (default ``../data``)."""
        root = self.get("data.root", "../data")
        return (self.path.parent / root).resolve()

    def data_path(self, dotted: str) -> Path:
        """Resolve a required, ``data.root``-relative path to an absolute one."""
        rel = self.require(dotted)
        return (self.data_root / rel).resolve()

    @property
    def output_dir(self) -> Path:
        """Absolute output dir (relative to the repo root, one level up)."""
        out = self.get("output.dir", "outputs/sonami")
        return (self.path.parent.parent.parent / out).resolve()

    # -- postgis ------------------------------------------------------------
    def pg_url(self) -> str:
        """SQLAlchemy URL for the PostGIS target, password from env.

        geopandas ``to_postgis`` writes through SQLAlchemy; psycopg2 is the
        underlying DB-API driver.
        """
        pg = self.raw.get("postgis", {})
        pw_env = pg.get("password_env", "SONAMI_PG_PASSWORD")
        password = os.environ.get(pw_env)
        if password is None:
            raise ConfigError(
                f"PostGIS password not found. Set the '{pw_env}' environment variable."
            )
        host = pg.get("host", "localhost")
        port = pg.get("port", 5432)
        dbname = pg.get("dbname", "sonami")
        user = pg.get("user", "sonami")
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"
