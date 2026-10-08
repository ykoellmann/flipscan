"""Session state: raw page files plus a small json describing edits.

Original images are never modified; delete/cut/rotate are only entries in
session.json, which is always written atomically.
"""
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageFilter

THUMB_WIDTH = 320
BLANK_INK_THRESHOLD = 0.002  # fraction of dark pixels in the inner area


def ink_fraction(img: Image.Image) -> float:
    """Dark-pixel share of the inner area (10% margin cut off all around), so
    pages that only carry a header/footer are not flagged as blank."""
    g = img.convert("L")
    g.thumbnail((600, 600))
    w, h = g.size
    inner = g.crop((int(w * .1), int(h * .1), int(w * .9), int(h * .9)))
    inner = inner.filter(ImageFilter.MedianFilter(3))  # drop scanner speckle
    px = inner.get_flattened_data() if hasattr(inner, "get_flattened_data") else inner.getdata()
    dark = sum(1 for p in px if p < 128)
    return dark / max(1, inner.size[0] * inner.size[1])


class Session:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self.data = {"created": datetime.now().isoformat(timespec="seconds"),
                     "next_sheet": 1, "next_page": 1, "pages": []}
        self.undo_stack: list[str] = []
        if (root / "session.json").exists():
            self.data = json.loads((root / "session.json").read_text())

    # --- lifecycle -------------------------------------------------
    @classmethod
    def create(cls, sessions_dir: Path) -> "Session":
        root = sessions_dir / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        (root / "pages").mkdir(parents=True)
        (root / "thumbs").mkdir()
        s = cls(root)
        s.save()
        return s

    @classmethod
    def list_all(cls, sessions_dir: Path) -> list["Session"]:
        return [cls(p.parent) for p in sorted(sessions_dir.glob("*/session.json"))]

    @classmethod
    def open_named(cls, sessions_dir: Path, name: str) -> "Session | None":
        return cls(sessions_dir / name) if (sessions_dir / name / "session.json").exists() else None

    @classmethod
    def open_latest(cls, sessions_dir: Path) -> "Session | None":
        found = sorted(p for p in sessions_dir.glob("*/session.json"))
        return cls(found[-1].parent) if found else None

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def pages(self) -> list[dict]:
        return self.data["pages"]

    def save(self) -> None:
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.root / "session.json")

    # --- adding pages ----------------------------------------------
    def new_sheet(self) -> int:
        n = self.data["next_sheet"]
        self.data["next_sheet"] = n + 1
        return n

    def add_page(self, image_path: Path, source: str, sheet: int, side: str,
                 rotation: int = 0, batch: int = 0) -> dict:
        """Move a freshly scanned JPEG into the session and create its thumb."""
        with self.lock:
            pid = self.data["next_page"]
            self.data["next_page"] = pid + 1
            name = f"{pid:05d}.jpg"
            dest = self.root / "pages" / name
            os.replace(image_path, dest)
            with Image.open(dest) as img:
                blank = ink_fraction(img) < BLANK_INK_THRESHOLD
                t = img.copy()
                t.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
                t.convert("RGB").save(self.root / "thumbs" / name, quality=80)
            page = {"id": pid, "file": name, "source": source, "sheet": sheet,
                    "side": side, "batch": batch, "rotation": rotation,
                    "deleted": False, "cut_before": False, "blank_suggested": blank}
            self.pages.append(page)
            self.save()
            return page

    def reorder_back_sides(self, pairs: list[tuple[dict, dict]]) -> None:
        """Place each back page right after its front page."""
        with self.lock:
            for front, back in pairs:
                self.pages.remove(back)
                self.pages.insert(self.pages.index(front) + 1, back)
            self.save()

    def discard_batch(self, batch: int) -> None:
        with self.lock:
            for p in [p for p in self.pages if p["batch"] == batch]:
                (self.root / "pages" / p["file"]).unlink(missing_ok=True)
                (self.root / "thumbs" / p["file"]).unlink(missing_ok=True)
                self.pages.remove(p)
            self.save()

    def next_batch(self) -> int:
        return max((p["batch"] for p in self.pages), default=0) + 1

    # --- editing (review) ------------------------------------------
    def _snapshot(self) -> None:
        self.undo_stack.append(json.dumps(self.pages))
        del self.undo_stack[:-200]

    def update(self, ids: list[int], rotate_by: int = 0, **fields) -> None:
        allowed = {"deleted", "cut_before", "rotation", "doc_name"}
        with self.lock:
            self._snapshot()
            for p in self.pages:
                if p["id"] in ids:
                    p["rotation"] = (p["rotation"] + rotate_by) % 360
                    for k, v in fields.items():
                        if k in allowed:
                            p[k] = v
            self.save()

    def undo(self) -> bool:
        with self.lock:
            if not self.undo_stack:
                return False
            self.data["pages"] = json.loads(self.undo_stack.pop())
            self.save()
            return True

    # --- documents ---------------------------------------------------
    def documents(self) -> list[list[dict]]:
        """Group kept pages into documents. A cut on a deleted page still
        separates the neighbours."""
        docs: list[list[dict]] = []
        pending = True
        for p in self.pages:
            if p["cut_before"]:
                pending = True
            if p["deleted"]:
                continue
            if pending:
                docs.append([])
                pending = False
            docs[-1].append(p)
        return docs
