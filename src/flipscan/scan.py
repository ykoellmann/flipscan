"""Scanning: wraps scanimage, writes raw pages into a Session."""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .config import Config
from .session import Session


def _scanimage(cfg: Config, source: str, outdir: Path, batch: bool) -> list[Path]:
    fmt = cfg.scan_format
    ext = {"jpeg": "jpg", "png": "png", "tiff": "tiff"}[fmt]
    cmd = ["scanimage", "--device", cfg.scanner, "--resolution", str(cfg.dpi),
           f"--format={fmt}", "--source", source,
           "-x", str(cfg.page_width_mm), "-y", str(cfg.page_height_mm)]
    if batch:
        cmd += [f"--batch={outdir}/p_%03d.{ext}", "--batch-start=1"]
        # exit code is non-zero once the feeder runs empty -> ignore
        subprocess.run(cmd, stderr=subprocess.DEVNULL)
    else:
        out = outdir / f"p_001.{ext}"
        with open(out, "wb") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.DEVNULL)
        if out.stat().st_size == 0:
            out.unlink()
    files = []
    for f in sorted(outdir.glob("p_*")):
        if f.stat().st_size == 0:  # scanimage leaves an empty file when the feeder runs dry
            f.unlink()
        else:
            files.append(f)
    return [_to_jpeg(f) for f in files]


def _to_jpeg(path: Path) -> Path:
    if path.suffix == ".jpg":
        return path
    dest = path.with_suffix(".jpg")
    with Image.open(path) as img:
        img.convert("RGB").save(dest, quality=95)
    path.unlink()
    return dest


def scan_adf_fronts(cfg: Config, outdir: Path) -> list[Path]:
    return _scanimage(cfg, "ADF", outdir, batch=True)


def scan_flatbed_page(cfg: Config, outdir: Path) -> Path | None:
    files = _scanimage(cfg, "Flatbed", outdir, batch=False)
    return files[0] if files else None


def add_adf_batch(cfg: Config, session: Session, fronts: list[Path],
                  backs: list[Path]) -> None:
    """Back sides arrive in reverse order after flipping the stack."""
    batch = session.next_batch()
    backs = list(reversed(backs))
    pairs = []
    for i, f in enumerate(fronts):
        sheet = session.new_sheet()
        fp = session.add_page(f, "ADF", sheet, "front", batch=batch)
        if i < len(backs):
            bp = session.add_page(backs[i], "ADF", sheet, "back",
                                  rotation=cfg.back_rotation, batch=batch)
            pairs.append((fp, bp))
    for b in backs[len(fronts):]:  # surplus backs: keep, appended as own sheets
        session.add_page(b, "ADF", session.new_sheet(), "back",
                         rotation=cfg.back_rotation, batch=batch)
    session.reorder_back_sides(pairs)


def add_flatbed_page(session: Session, image: Path, batch: int) -> None:
    session.add_page(image, "Flatbed", session.new_sheet(), "front", batch=batch)


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="flipscan_"))
