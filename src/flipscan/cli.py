import argparse
from importlib import resources
import shutil
import sys

from . import config as config_mod
from .session import Session


def _ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        sys.exit(0)
    return ans or default


def _get_session(cfg, new: bool, name: str | None = None) -> Session:
    cfg.sessions_path.mkdir(parents=True, exist_ok=True)
    if name:
        s = Session.open_named(cfg.sessions_path, name)
        if s is None:
            sys.exit(f"Session {name} not found (see: flipscan sessions).")
        return s
    s = None if new else Session.open_latest(cfg.sessions_path)
    if s is None:
        s = Session.create(cfg.sessions_path)
        print(f"New session: {s.name}")
    else:
        print(f"Session: {s.name} ({len(s.pages)} pages)")
    return s


def _ready(cfg, scan) -> bool:
    """Check the scanner before scanning. If it is stopped (jam, cancel on the
    device, ...), say why and wait until it recovers. False = user chose to quit."""
    while True:
        why = scan.problem(scan.scanner_status(cfg))
        if why is None:
            return True
        print(f"  ! Scanner not ready: {why}.")
        print("    Clear the problem on the device (open/close the feeder cover, remove stuck paper).")
        ans = _ask("    [Enter] check again  [s] scan anyway  [q] quit > ")
        if ans == "q":
            return False
        if ans == "s":
            return True


def _report_empty(scan, what: str) -> None:
    print(f"  0 {what}." + (f" scanimage: {scan.last_error}" if scan.last_error else ""))


def _adf_batch(cfg, session, scan) -> None:
    """One ADF batch: fronts, flip, backs. Every step can be redone: fronts after
    scanning them, fronts or backs after the backs are in. The other side is kept
    (so a fixed rescan can complete the pair)."""
    dirs = []

    def fresh():
        d = scan.tmpdir()
        dirs.append(d)
        return d

    def rescan(old, what):
        shutil.rmtree(old, ignore_errors=True)
        d = fresh()
        files = scan.scan_adf_fronts(cfg, d)
        print(f"  {len(files)} {what} side(s).")
        if not files and scan.last_error:
            print(f"  scanimage: {scan.last_error}")
        return d, files

    try:
        ftmp = fresh()
        fronts = scan.scan_adf_fronts(cfg, ftmp)
        if not fronts:
            _report_empty(scan, "front sides (feeder empty or scan failed)")
            return
        print(f"  {len(fronts)} front side(s).")
        why = scan.problem(scan.scanner_status(cfg))
        if why:  # e.g. jam in the middle of the stack: the batch is incomplete
            print(f"  ! Scan was interrupted: {why}.")
            if _ask("    [Enter] discard this batch and start over  [k] keep the pages so far > ") != "k":
                return
        while True:
            ans = _ask("Flip the stack and reload -> [Enter] scan back sides  [r] rescan fronts  "
                       "[n] single-sided  [d] discard > ")
            if ans == "n":
                scan.add_adf_batch(cfg, session, fronts, [])
                return
            if ans == "d":
                return
            if ans != "r":
                break
            input("Put the front sides back into the feeder, then press Enter... ")
            if not _ready(cfg, scan):
                return
            ftmp, fronts = rescan(ftmp, "front")
            if not fronts:
                return
        if not _ready(cfg, scan):
            return
        btmp = fresh()
        backs = scan.scan_adf_fronts(cfg, btmp)
        print(f"  {len(backs)} back side(s).")
        if not backs and scan.last_error:
            print(f"  scanimage: {scan.last_error}")
        while True:
            if len(backs) == len(fronts):
                ans = _ask(f"{len(fronts)} fronts + {len(backs)} backs -> [Enter] accept  "
                           "[r] rescan backs  [f] rescan fronts  [d] discard > ")
                if ans == "":
                    break
            else:
                ans = _ask(f"Count mismatch ({len(fronts)} fronts vs {len(backs)} backs). "
                           "[r] rescan backs  [f] rescan fronts  [k] keep anyway  [d] discard > ")
                if ans == "k":
                    break
            if ans == "d":
                return
            if ans == "f":
                input("Put the front sides back into the feeder, then press Enter... ")
                if not _ready(cfg, scan):
                    return
                ftmp, fronts = rescan(ftmp, "front")
                if not fronts:
                    return
            elif ans == "r":
                input("Reload the back sides, then press Enter... ")
                if not _ready(cfg, scan):
                    return
                btmp, backs = rescan(btmp, "back")
        scan.add_adf_batch(cfg, session, fronts, backs)
        print("  Batch added.")
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


def cmd_scan(cfg, args):
    from . import scan
    if args.height:
        cfg.page_height_mm = args.height
    if args.back_rotation is not None:
        cfg.back_rotation = args.back_rotation
    if args.dpi:
        cfg.dpi = args.dpi
    if not cfg.scanner:
        sys.exit("No scanner configured (config.toml, see config.example.toml).")
    session = _get_session(cfg, args.new, args.session)
    while True:
        mode = _ask("Load paper -> [Enter] scan ADF  [f] scan flatbed  [q] quit > ", "a")
        if mode == "q":
            break
        if not _ready(cfg, scan):
            break
        tmp = scan.tmpdir()
        try:
            if mode == "f":
                batch = session.next_batch()
                while True:
                    img = scan.scan_flatbed_page(cfg, tmp)
                    if img is None:
                        _report_empty(scan, "pages scanned")
                    else:
                        scan.add_flatbed_page(session, img, batch)
                        print(f"  Page saved ({len(session.pages)} total)")
                    if _ask("Place next page -> [Enter] scan  [n] done > ") == "n":
                        break
                    tmp = scan.tmpdir()
                continue
            _adf_batch(cfg, session, scan)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"Done. Next: flipscan review  (session {session.name})")


def cmd_review(cfg, args):
    from .review import create_app
    session = _get_session(cfg, False, args.session)
    app = create_app(cfg, session)
    print(f"Review: http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


def cmd_upload(cfg, args):
    from .export import export_pdfs
    from .upload import upload_session
    session = _get_session(cfg, False, args.session)
    if args.export:
        export_pdfs(session)
    ok, failed = upload_session(cfg, session)
    print(f"{len(ok)} uploaded, {len(failed)} failed (kept in export/).")
    sys.exit(1 if failed else 0)


def cmd_sessions(cfg, args):
    for s in Session.list_all(cfg.sessions_path):
        if not s.pages:
            continue
        kept = sum(not p["deleted"] for p in s.pages)
        pending = len(list((s.root / "export").glob("*.pdf"))) if (s.root / "export").exists() else 0
        print(f"{s.name}  [{'done' if s.status == 'done' else 'open'}]  "
              f"{len(s.pages)} pages ({kept} kept), {pending} PDF(s) in outbox")


def cmd_ocr(cfg, args):
    from .ocr import run_ocr
    s = _get_session(cfg, False, args.session)
    print(f"{run_ocr(s)} page number(s) detected. In the review UI, C accepts the suggested cuts.")


def cmd_init_config(cfg, args):
    """Write a commented starter config (bundled example) unless one exists."""
    target = config_mod.CONFIG_PATH
    if target.exists():
        sys.exit(f"{target} already exists.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(resources.files("flipscan") / "config.example.toml", target)
    print(f"Written: {target}  (find your scanner id with `scanimage -L`)")


def main():
    ap = argparse.ArgumentParser(prog="flipscan")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--session", help="session name (default: latest)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="scan batches into a session")
    p.add_argument("--new", action="store_true", help="start a new session")
    p.add_argument("--height", type=int, help="scan height in mm (config: page_height_mm)")
    p.add_argument("--back-rotation", type=int, choices=(0, 90, 180, 270),
                   help="rotation of back sides (config: back_rotation)")
    p.add_argument("--dpi", type=int, help="resolution (config: dpi)")
    p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("review", help="start the review UI")
    p.add_argument("--port", type=int, default=5000)
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("upload", help="upload exported PDFs (also retries failed ones)")
    p.add_argument("--export", action="store_true", help="re-export before uploading")
    p.set_defaults(fn=cmd_upload)
    sub.add_parser("sessions", help="list sessions").set_defaults(fn=cmd_sessions)
    sub.add_parser("ocr", help="detect page numbers (suggests cuts)").set_defaults(fn=cmd_ocr)
    sub.add_parser("init-config", help="write an example config to ~/.config/flipscan").set_defaults(fn=cmd_init_config)
    args = ap.parse_args()
    from pathlib import Path
    cfg = config_mod.load(Path(args.config) if args.config else None)
    args.fn(cfg, args)


if __name__ == "__main__":
    main()
