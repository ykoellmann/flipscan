"""Upload backends plus an outbox: failed files stay in <session>/export/
and are retried by the next upload run."""
import shutil
import subprocess
import urllib.request
import uuid
from pathlib import Path

from .config import Config
from .session import Session


def _post_paperless(up, pdf: Path) -> bool:
    """POST multipart to <url>/api/documents/post_document/ (stdlib only)."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
            f"filename=\"{pdf.name}\"\r\nContent-Type: application/pdf\r\n\r\n").encode() \
        + pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(up.url.rstrip("/") + "/api/documents/post_document/", data=body,
        headers={"Authorization": f"Token {up.token}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return 200 <= r.status < 300


def _send(cfg: Config, pdf: Path) -> bool:
    up = cfg.upload
    try:
        if up.backend == "scp":
            target = f"{up.user}@{up.host}:{up.path}/" if up.user else f"{up.host}:{up.path}/"
            return subprocess.run(["scp", "-q", str(pdf), target],
                                  capture_output=True, timeout=300).returncode == 0
        if up.backend == "paperless":
            return _post_paperless(up, pdf)
        if up.backend == "folder":
            shutil.copy2(pdf, Path(up.path).expanduser() / pdf.name)
            return True
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return False


def upload_session(cfg: Config, session: Session) -> tuple[list[Path], list[Path]]:
    """Returns (uploaded, failed). Uploaded files are moved to export/done/."""
    out = session.root / "export"
    done = out / "done"
    done.mkdir(parents=True, exist_ok=True)
    ok, failed = [], []
    for pdf in sorted(out.glob("*.pdf")):
        if _send(cfg, pdf):
            shutil.move(pdf, done / pdf.name)
            ok.append(pdf)
        else:
            failed.append(pdf)
    return ok, failed
