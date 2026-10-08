import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path("~/.config/flipscan/config.toml").expanduser()


@dataclass
class Upload:
    backend: str = "folder"
    host: str = ""
    user: str = ""
    path: str = ""
    url: str = ""
    token: str = ""


@dataclass
class Config:
    scanner: str = ""
    dpi: int = 300
    page_width_mm: int = 210
    page_height_mm: int = 297
    scan_format: str = "jpeg"
    back_rotation: int = 0
    sessions_dir: str = "~/Scans/sessions"
    upload: Upload = field(default_factory=Upload)

    @property
    def sessions_path(self) -> Path:
        return Path(self.sessions_dir).expanduser()


class ConfigError(SystemExit):
    pass


def _validate(cfg: Config) -> None:
    if cfg.scan_format not in ("jpeg", "png", "tiff"):
        raise ConfigError(f"config: scan_format must be jpeg|png|tiff, got {cfg.scan_format!r}")
    if cfg.back_rotation not in (0, 90, 180, 270):
        raise ConfigError("config: back_rotation must be 0, 90, 180 or 270")
    if cfg.upload.backend not in ("scp", "folder", "paperless"):
        raise ConfigError("config: upload.backend must be scp|folder|paperless")


def load(path: Path | None = None) -> Config:
    """Order: explicit path, $FLIPSCAN_CONFIG, ~/.config/flipscan/config.toml, defaults."""
    env = os.environ.get("FLIPSCAN_CONFIG")
    path = path or (Path(env).expanduser() if env else CONFIG_PATH)
    cfg = Config()
    if not path.exists():
        return cfg  # defaults
    raw = tomllib.loads(path.read_text())
    up = raw.pop("upload", {})
    for k, v in raw.items():
        if not hasattr(cfg, k):
            raise ConfigError(f"config: unknown key {k!r}")
        setattr(cfg, k, v)
    for k, v in up.items():
        if not hasattr(cfg.upload, k):
            raise ConfigError(f"config: unknown key upload.{k!r}")
        setattr(cfg.upload, k, v)
    _validate(cfg)
    return cfg
