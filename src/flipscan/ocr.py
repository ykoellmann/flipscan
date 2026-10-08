"""Optional: detect 'Seite 1 von 3' style page numbers via tesseract and
suggest cuts before pages that read 1/N. Suggestions only."""
import os
import re
import subprocess
from pathlib import Path

from .session import Session

PATTERN = re.compile(r"(?:seite|page|s\.|p\.)\s*(\d{1,3})\s*(?:von|of|/)\s*(\d{1,3})", re.I)


USER_TESSDATA = Path("~/.local/share/tessdata").expanduser()
if "TESSDATA_PREFIX" not in os.environ and (USER_TESSDATA / "eng.traineddata").exists():
    os.environ["TESSDATA_PREFIX"] = str(USER_TESSDATA)  # no root needed for language data


def _langs() -> str:
    out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout.split()
    wanted = [l for l in ("deu", "eng") if l in out]
    return "+".join(wanted) or "eng"


def find_page_of(text: str) -> tuple[int, int] | None:
    m = PATTERN.search(text)
    if not m:
        return None
    n, total = int(m.group(1)), int(m.group(2))
    return (n, total) if 1 <= n <= total else None


def run_ocr(session: Session) -> int:
    lang = _langs()
    found = 0
    for p in session.pages:
        if p.get("ocr_done") or p["deleted"]:
            continue
        img = session.root / "pages" / p["file"]
        text = subprocess.run(["tesseract", str(img), "-", "-l", lang],
                              capture_output=True, text=True).stdout
        p["ocr_done"] = True
        po = find_page_of(text)
        if po:
            p["page_of"] = list(po)
            found += 1
    seen_first = False
    for p in session.pages:
        p["cut_suggested"] = bool(p.get("page_of") and p["page_of"][0] == 1 and seen_first)
        if not p["deleted"]:
            seen_first = True
    session.save()
    return found
