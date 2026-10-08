import io
import re
from pathlib import Path

import img2pdf
from PIL import Image

from .session import Session


def _page_bytes(session: Session, page: dict) -> bytes:
    path = session.root / "pages" / page["file"]
    rot = page["rotation"] % 360
    if rot == 0:
        return path.read_bytes()
    with Image.open(path) as img:
        out = img.rotate(-rot, expand=True)  # PIL rotates counter-clockwise
        buf = io.BytesIO()
        out.convert("RGB").save(buf, "JPEG", quality=95)
        return buf.getvalue()


def export_pdfs(session: Session) -> list[Path]:
    """One PDF per document into <session>/export/. Previously exported
    files are replaced."""
    out = session.root / "export"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.pdf"):
        old.unlink()
    result = []
    for i, doc in enumerate(session.documents(), 1):
        name = re.sub(r"[^\w .-]", "_", doc[0].get("doc_name", "")).strip()
        path = out / (f"{name}.pdf" if name else f"scan_{session.name}_{i:02d}.pdf")
        if path.exists():  # two docs with the same name
            path = out / f"{path.stem}_{i:02d}.pdf"
        path.write_bytes(img2pdf.convert([_page_bytes(session, p) for p in doc]))
        result.append(path)
    return result
