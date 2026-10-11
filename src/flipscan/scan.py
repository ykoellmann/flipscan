"""Scanning: wraps scanimage, writes raw pages into a Session."""
import re
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from .config import Config
from .session import Session


last_error = ""  # last stderr line of the most recent scanimage call

ADF_PROBLEMS = {
    "ScannerAdfJam": "paper jam in the document feeder",
    "ScannerAdfHatchOpen": "feeder cover is open",
    "ScannerAdfMispick": "paper was not picked up (misfeed)",
    "ScannerAdfMultipickDetected": "multiple sheets were pulled in at once",
    "ScannerAdfInputTrayFailed": "feeder input tray failed",
    "ScannerAdfInputTrayOverloaded": "feeder input tray is overloaded",
}


def scanner_ip(cfg: Config) -> str | None:
    """Find the scanner's IP from `scanimage -L` (airscan lists it as ip=...)."""
    out = subprocess.run(["scanimage", "-L"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if cfg.scanner in line:
            m = re.search(r"ip=([\d.]+)", line) or re.search(r"//([\d.]+)", line)
            if m:
                return m.group(1)
    return None


def parse_status(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    ns = {"pwg": "http://www.pwg.org/schemas/2010/12/sm",
          "scan": "http://schemas.hp.com/imaging/escl/2011/05/03"}
    return {"state": root.findtext("pwg:State", "", ns), "adf": root.findtext("scan:AdfState", "", ns)}


def scanner_status(cfg: Config) -> dict | None:
    """eSCL ScannerStatus, or None if the scanner cannot be reached."""
    ip = scanner_ip(cfg)
    if not ip:
        return None
    try:
        with urllib.request.urlopen(f"http://{ip}/eSCL/ScannerStatus", timeout=5) as r:
            return parse_status(r.read().decode())
    except (OSError, ET.ParseError):
        return None


def problem(status: dict | None) -> str | None:
    """Human readable reason why the scanner will refuse to scan, or None if ready."""
    if status is None:
        return "scanner is not reachable (powered off / asleep / network?)"
    if status["adf"] in ADF_PROBLEMS:
        return ADF_PROBLEMS[status["adf"]]
    if status["state"] in ("Stopped", "Down"):
        return f"scanner reports state '{status['state']}' (cancelled job or error on the device)"
    if status["state"] == "Processing":
        return "scanner is still busy with a previous job"
    return None


def _scanimage(cfg: Config, source: str, outdir: Path, batch: bool) -> list[Path]:
    fmt = cfg.scan_format
    ext = {"jpeg": "jpg", "png": "png", "tiff": "tiff"}[fmt]
    cmd = ["scanimage", "--device", cfg.scanner, "--resolution", str(cfg.dpi),
           f"--format={fmt}", "--source", source,
           "-x", str(cfg.page_width_mm), "-y", str(cfg.page_height_mm)]
    if batch:
        cmd += [f"--batch={outdir}/p_%03d.{ext}", "--batch-start=1"]
        # exit code is non-zero once the feeder runs empty -> ignore
        res = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    else:
        out = outdir / f"p_001.{ext}"
        with open(out, "wb") as f:
            res = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
        if out.stat().st_size == 0:
            out.unlink()
    global last_error
    lines = [l for l in res.stderr.strip().splitlines() if l.strip()]
    last_error = lines[-1] if lines else ""
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
