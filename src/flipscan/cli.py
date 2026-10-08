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
            sys.exit(f"Session {name} nicht gefunden (flipscan sessions).")
        return s
    s = None if new else Session.open_latest(cfg.sessions_path)
    if s is None:
        s = Session.create(cfg.sessions_path)
        print(f"Neue Session: {s.name}")
    else:
        print(f"Session: {s.name} ({len(s.pages)} Seiten)")
    return s


def cmd_scan(cfg, args):
    from . import scan
    if args.height:
        cfg.page_height_mm = args.height
    if args.back_rotation is not None:
        cfg.back_rotation = args.back_rotation
    if args.dpi:
        cfg.dpi = args.dpi
    if not cfg.scanner:
        sys.exit("Kein Scanner konfiguriert (config.toml, siehe config.example.toml).")
    session = _get_session(cfg, args.new, args.session)
    while True:
        mode = _ask("Charge hinzufuegen: [a] ADF  [f] Flachbett  [q] Ende > ", "a")
        if mode == "q":
            break
        tmp = scan.tmpdir()
        try:
            if mode == "f":
                batch = session.next_batch()
                while True:
                    input("Seite auflegen, Enter... ")
                    img = scan.scan_flatbed_page(cfg, tmp)
                    if img is None:
                        print("  Nichts gescannt.")
                    else:
                        scan.add_flatbed_page(session, img, batch)
                        print(f"  Seite gespeichert ({len(session.pages)} gesamt)")
                    if _ask("Weitere Seite? [Enter] ja  [n] fertig > ") == "n":
                        break
                    tmp = scan.tmpdir()
                continue
            input("Vorderseiten einlegen, Enter... ")
            fronts = scan.scan_adf_fronts(cfg, tmp)
            print(f"  {len(fronts)} Vorderseite(n).")
            if not fronts:
                continue
            if _ask("Rueckseiten scannen? [Enter] ja  [n] einseitig > ") == "n":
                scan.add_adf_batch(cfg, session, fronts, [])
                continue
            input("Stapel umdrehen, Enter... ")
            btmp = scan.tmpdir()
            while True:
                backs = scan.scan_adf_fronts(cfg, btmp)
                print(f"  {len(backs)} Rueckseite(n).")
                if len(backs) == len(fronts):
                    break
                ans = _ask(f"Anzahl passt nicht ({len(fronts)} vs {len(backs)}). "
                           "[r] Rueckseiten neu  [t] trotzdem  [v] Charge verwerfen > ")
                if ans == "t":
                    break
                if ans == "v":
                    backs = None
                    break
                shutil.rmtree(btmp, ignore_errors=True)
                btmp = scan.tmpdir()
                input("Stapel erneut einlegen, Enter... ")
            if backs is not None:
                scan.add_adf_batch(cfg, session, fronts, backs)
                print("  Charge uebernommen.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"Fertig. Weiter mit: flipscan review  (Session {session.name})")


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
    print(f"{len(ok)} hochgeladen, {len(failed)} fehlgeschlagen (bleiben in export/).")
    sys.exit(1 if failed else 0)


def cmd_sessions(cfg, args):
    for s in Session.list_all(cfg.sessions_path):
        kept = sum(not p["deleted"] for p in s.pages)
        pending = len(list((s.root / "export").glob("*.pdf"))) if (s.root / "export").exists() else 0
        print(f"{s.name}  {len(s.pages)} Seiten ({kept} behalten), {pending} PDF(s) im Outbox")


def cmd_ocr(cfg, args):
    from .ocr import run_ocr
    s = _get_session(cfg, False, args.session)
    print(f"{run_ocr(s)} Seitenzahl(en) erkannt. Im Review: C akzeptiert Schnittvorschlaege.")


def cmd_init_config(cfg, args):
    """Write a commented starter config (bundled example) unless one exists."""
    target = config_mod.CONFIG_PATH
    if target.exists():
        sys.exit(f"{target} existiert bereits.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(resources.files("flipscan") / "config.example.toml", target)
    print(f"Geschrieben: {target}  (Scanner-ID mit `scanimage -L` ermitteln)")


def main():
    ap = argparse.ArgumentParser(prog="flipscan")
    ap.add_argument("--config", help="Pfad zur config.toml")
    ap.add_argument("--session", help="Session-Name (Standard: neueste)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="Chargen in eine Session scannen")
    p.add_argument("--new", action="store_true", help="neue Session beginnen")
    p.add_argument("--height", type=int, help="Scanhoehe in mm (Config: page_height_mm)")
    p.add_argument("--back-rotation", type=int, choices=(0, 90, 180, 270),
                   help="Drehung der Rueckseiten (Config: back_rotation)")
    p.add_argument("--dpi", type=int, help="Aufloesung (Config: dpi)")
    p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("review", help="Review-UI starten")
    p.add_argument("--port", type=int, default=5000)
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("upload", help="Exportierte PDFs hochladen (Retry)")
    p.add_argument("--export", action="store_true", help="vorher neu exportieren")
    p.set_defaults(fn=cmd_upload)
    sub.add_parser("sessions", help="Sessions auflisten").set_defaults(fn=cmd_sessions)
    sub.add_parser("ocr", help="Seitenzahlen erkennen (Schnittvorschlaege)").set_defaults(fn=cmd_ocr)
    sub.add_parser("init-config", help="Beispiel-Config nach ~/.config/flipscan schreiben").set_defaults(fn=cmd_init_config)
    args = ap.parse_args()
    from pathlib import Path
    cfg = config_mod.load(Path(args.config) if args.config else None)
    args.fn(cfg, args)


if __name__ == "__main__":
    main()
