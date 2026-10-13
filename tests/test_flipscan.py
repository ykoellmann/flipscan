from PIL import Image, ImageDraw

from flipscan.config import Config, Upload
from flipscan.export import export_pdfs
from flipscan.review import create_app
from flipscan.scan import add_adf_batch
from flipscan.session import Session
from flipscan.upload import upload_session


def make_img(path, text=True):
    """A4 @300dpi: thin real text lines (the case that broke blank detection) or blank."""
    from PIL import ImageFont
    img = Image.new("RGB", (2480, 3508), "white")
    if text:
        d = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=28)
        for i in range(40):
            d.text((300, 400 + i * 70), "Sehr geehrte Damen und Herren, anbei die Rechnung Nr. 12345",
                   fill="black", font=font)
    img.save(path, "JPEG")
    return path


def test_flow(tmp_path):
    cfg = Config(back_rotation=180, sessions_dir=str(tmp_path / "s"),
                 upload=Upload(backend="folder", path=str(tmp_path / "consume")))
    (tmp_path / "consume").mkdir()
    s = Session.create(cfg.sessions_path)
    fronts = [make_img(tmp_path / f"f{i}.jpg") for i in range(3)]
    # backs come in reverse order; physical back #0 is the one scanned last
    backs = [make_img(tmp_path / f"b{i}.jpg", text=(i != 1)) for i in range(3)]
    add_adf_batch(cfg, s, fronts, backs)
    assert [p["side"] for p in s.pages] == ["front", "back"] * 3
    assert s.pages[1]["rotation"] == 180
    blanks = [p for p in s.pages if p["ink"] <= 0.02]
    assert len(blanks) == 1 and blanks[0]["side"] == "back"

    # cut before sheet 3, delete blank
    s.update([blanks[0]["id"]], deleted=True)
    s.update([s.pages[4]["id"]], cut_before=True)
    assert [len(d) for d in s.documents()] == [3, 2]
    s.undo(); s.undo()
    assert [len(d) for d in s.documents()] == [6]
    s.update([s.pages[4]["id"]], cut_before=True)
    s.update([s.pages[1]["id"]], rotation=90)

    # persisted state reloads
    assert Session(s.root).pages == s.pages

    pdfs = export_pdfs(s)
    assert len(pdfs) == 2 and all(p.stat().st_size > 0 for p in pdfs)
    ok, failed = upload_session(cfg, s)
    assert len(ok) == 2 and not failed
    assert len(list((tmp_path / "consume").glob("*.pdf"))) == 2
    assert not (s.root / "export").exists()  # local copies removed after upload


def test_api(tmp_path):
    cfg = Config(sessions_dir=str(tmp_path))
    s = Session.create(tmp_path)
    s.add_page(make_img(tmp_path / "a.jpg"), "Flatbed", s.new_sheet(), "front")
    c = create_app(cfg, s).test_client()
    r = c.post("/api/update", json={"ids": [1], "deleted": True}).get_json()
    assert r["pages"][0]["deleted"] and r["documents"] == []
    assert c.get("/thumbs/00001.jpg").status_code == 200
    assert c.get("/").status_code == 200


def test_names_and_ocr(tmp_path):
    from flipscan.ocr import find_page_of
    assert find_page_of("Seite 2 von 5") == (2, 5)
    assert find_page_of("Page 1 of 3") == (1, 3)
    assert find_page_of("Seite 4 von 3") is None
    s = Session.create(tmp_path)
    for i in range(2):
        s.add_page(make_img(tmp_path / f"{i}.jpg"), "Flatbed", s.new_sheet(), "front")
    s.update([s.pages[0]["id"]], doc_name="Strom/Rechnung")
    assert export_pdfs(s)[0].name == "Strom_Rechnung.pdf"
    assert len(Session.list_all(tmp_path)) == 1


def test_paperless_backend(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"] = self.path
            got["auth"] = self.headers["Authorization"]
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200); self.end_headers(); self.wfile.write(b'"ok"')
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    cfg = Config(upload=Upload(backend="paperless", url=f"http://127.0.0.1:{srv.server_port}", token="T"))
    s = Session.create(tmp_path)
    s.add_page(make_img(tmp_path / "a.jpg"), "Flatbed", s.new_sheet(), "front")
    export_pdfs(s)
    ok, failed = upload_session(cfg, s)
    assert len(ok) == 1 and not failed
    assert got["path"] == "/api/documents/post_document/" and got["auth"] == "Token T"
    assert b"%PDF" in got["body"]


def test_ocr_end_to_end(tmp_path):
    import shutil
    import pytest
    from PIL import ImageFont
    from flipscan.ocr import run_ocr, _langs
    if not shutil.which("tesseract") or "eng" not in _langs():
        pytest.skip("tesseract with eng/deu data not available")
    s = Session.create(tmp_path)
    for n in (1, 2, 1):
        img = Image.new("RGB", (1200, 400), "white")
        ImageDraw.Draw(img).text((50, 150), f"Seite {n} von 2", fill="black",
                                 font=ImageFont.load_default(size=80))
        img.save(tmp_path / "t.jpg")
        s.add_page(tmp_path / "t.jpg", "Flatbed", s.new_sheet(), "front")
    assert run_ocr(s) == 3
    assert [p["cut_suggested"] for p in s.pages] == [False, False, True]


def test_config(tmp_path):
    import pytest
    from flipscan import config
    f = tmp_path / "c.toml"
    f.write_text('dpi = 600\n[upload]\nbackend = "folder"\npath = "/x"\n')
    c = config.load(f)
    assert c.dpi == 600 and c.upload.path == "/x"
    f.write_text("bogus = 1\n")
    with pytest.raises(SystemExit):
        config.load(f)
    f.write_text("back_rotation = 45\n")
    with pytest.raises(SystemExit):
        config.load(f)


def test_rotate_by(tmp_path):
    s = Session.create(tmp_path)
    s.add_page(make_img(tmp_path / "a.jpg"), "ADF", 1, "back", rotation=90)
    s.add_page(make_img(tmp_path / "b.jpg"), "ADF", 2, "back", rotation=270)
    s.update([1, 2], rotate_by=180)
    assert [p["rotation"] for p in s.pages] == [270, 90]
    s.undo()
    assert [p["rotation"] for p in s.pages] == [90, 270]


def test_multi_session_and_done(tmp_path):
    cfg = Config(sessions_dir=str(tmp_path / "s"),
                 upload=Upload(backend="folder", path=str(tmp_path / "c")))
    (tmp_path / "c").mkdir()
    a = Session.create(cfg.sessions_path)
    a.add_page(make_img(tmp_path / "a.jpg"), "Flatbed", a.new_sheet(), "front")
    import time; time.sleep(1.1)
    b = Session.create(cfg.sessions_path)
    b.add_page(make_img(tmp_path / "b.jpg"), "Flatbed", b.new_sheet(), "front")
    b.add_page(make_img(tmp_path / "b2.jpg"), "Flatbed", b.new_sheet(), "front")
    c = create_app(cfg, b).test_client()
    names = [s["name"] for s in c.get("/api/sessions").get_json()]
    assert names == [b.name, a.name]
    assert c.get("/api/session").get_json()["name"] == b.name
    assert c.post("/api/select", json={"name": a.name}).get_json()["pages"].__len__() == 1
    assert c.post("/api/select", json={"name": "nope"}).status_code == 404
    # upload success marks the selected session done, persisted
    r = c.post("/api/export", json={"upload": True}).get_json()
    assert r["status"] == "done" and Session(a.root).status == "done"
    assert next(s for s in c.get("/api/sessions").get_json() if s["name"] == a.name)["status"] == "done"
    # manual toggle + scanning reopens
    c.post("/api/status", json={"status": "open"})
    assert Session(a.root).status == "open"
    c.post("/api/status", json={"status": "done"})
    a2 = Session(a.root)
    a2.add_page(make_img(tmp_path / "x.jpg"), "Flatbed", a2.new_sheet(), "front")
    assert Session(a.root).status == "open"


def test_empty_sessions_hidden(tmp_path):
    cfg = Config(sessions_dir=str(tmp_path))
    s = Session.create(tmp_path)
    assert create_app(cfg, s).test_client().get("/api/sessions").get_json() == []


def test_blank_detection():
    from flipscan.session import non_paper_fraction
    from PIL import ImageFont
    white = Image.new("RGB", (2480, 3508), "white")
    assert non_paper_fraction(white) < 0.001
    gray = Image.new("RGB", (2480, 3508), (225, 225, 225))  # dingy paper, no content
    assert non_paper_fraction(gray) < 0.001
    import random
    random.seed(1)
    noisy = white.copy(); px = noisy.load()
    for _ in range(3000):
        px[random.randrange(2480), random.randrange(3508)] = (200, 200, 200)
    assert non_paper_fraction(noisy) < 0.02
    text = white.copy(); d = ImageDraw.Draw(text)
    for i in range(40):
        d.text((300, 400 + i * 70), "Sehr geehrte Damen und Herren, anbei die Rechnung",
               fill="black", font=ImageFont.load_default(size=28))
    assert non_paper_fraction(text) > 0.02
    # only a header line inside the 10% margin -> still counts as blank (margin ignored)
    hdr = white.copy(); ImageDraw.Draw(hdr).text((300, 100), "Briefkopf", fill="black",
                                                 font=ImageFont.load_default(size=60))
    assert non_paper_fraction(hdr) < 0.001
    assert non_paper_fraction(Image.new("RGB", (2480, 3508), "black")) == 1.0


def test_blank_threshold_from_config(tmp_path):
    cfg = Config(sessions_dir=str(tmp_path), blank_threshold=0.99999)  # nearly everything "not blank"
    s = Session.create(tmp_path)
    s.add_page(make_img(tmp_path / "w.jpg", text=False), "Flatbed", s.new_sheet(), "front")
    s.add_page(make_img(tmp_path / "t.jpg"), "Flatbed", s.new_sheet(), "front")
    pages = create_app(cfg, s).test_client().get("/api/session").get_json()["pages"]
    assert [p["blank_suggested"] for p in pages] == [True, False]


def test_legacy_pages_remeasured(tmp_path):
    s = Session.create(tmp_path)
    s.add_page(make_img(tmp_path / "t.jpg"), "Flatbed", s.new_sheet(), "front")
    del s.pages[0]["ink"]; s.pages[0]["blank_suggested"] = True  # old buggy format
    s.save()
    s2 = Session(s.root); s2.ensure_ink()
    assert s2.pages[0]["ink"] > 0.02 and "blank_suggested" not in s2.pages[0]


def test_delete_session(tmp_path):
    cfg = Config(sessions_dir=str(tmp_path))
    a = Session.create(tmp_path)
    a.add_page(make_img(tmp_path / "a.jpg"), "Flatbed", a.new_sheet(), "front")
    c = create_app(cfg, a).test_client()
    assert c.delete("/api/sessions/..%2F..").status_code == 404
    assert c.delete("/api/sessions/nope").status_code == 404
    r = c.delete(f"/api/sessions/{a.name}")
    assert r.status_code == 200 and r.get_json()["name"] is None
    assert not a.root.exists() and c.get("/api/sessions").get_json() == []


def test_failed_upload_keeps_pdf(tmp_path):
    cfg = Config(upload=Upload(backend="folder", path=str(tmp_path / "missing")))
    s = Session.create(tmp_path)
    s.add_page(make_img(tmp_path / "a.jpg"), "Flatbed", s.new_sheet(), "front")
    export_pdfs(s)
    ok, failed = upload_session(cfg, s)
    assert not ok and len(failed) == 1 and failed[0].exists() and s.status == "open"


JAM_XML = """<?xml version="1.0" encoding="UTF-8"?><scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"><pwg:Version>2.9</pwg:Version><pwg:State>Stopped</pwg:State><scan:AdfState>ScannerAdfJam</scan:AdfState></scan:ScannerStatus>"""
IDLE_XML = JAM_XML.replace("Stopped", "Idle").replace("ScannerAdfJam", "ScannerAdfLoaded")


def test_scanner_status_diagnosis():
    from flipscan import scan
    st = scan.parse_status(JAM_XML)
    assert st == {"state": "Stopped", "adf": "ScannerAdfJam"}
    assert "jam" in scan.problem(st)
    assert scan.problem(scan.parse_status(IDLE_XML)) is None
    assert "not reachable" in scan.problem(None)
    assert "Stopped" in scan.problem({"state": "Stopped", "adf": "ScannerAdfEmpty"})


def test_ready_waits_until_scanner_recovers(monkeypatch, capsys):
    import builtins
    from flipscan import cli, scan
    states = iter([scan.parse_status(JAM_XML), scan.parse_status(JAM_XML), scan.parse_status(IDLE_XML)])
    monkeypatch.setattr(scan, "scanner_status", lambda cfg: next(states))
    monkeypatch.setattr(builtins, "input", lambda *_: "")  # Enter = check again
    assert cli._ready(Config(), scan) is True
    assert "paper jam" in capsys.readouterr().out
    monkeypatch.setattr(scan, "scanner_status", lambda cfg: scan.parse_status(JAM_XML))
    monkeypatch.setattr(builtins, "input", lambda *_: "q")
    assert cli._ready(Config(), scan) is False


def test_move_pages(tmp_path):
    cfg = Config(sessions_dir=str(tmp_path))
    s = Session.create(tmp_path)
    for i in range(4):
        s.add_page(make_img(tmp_path / f"{i}.jpg", text=False), "Flatbed", s.new_sheet(), "front")
    c = create_app(cfg, s).test_client()
    ids = lambda r: [p["id"] for p in r["pages"]]
    assert ids(c.post("/api/move", json={"id": 1, "to": 2}).get_json()) == [2, 3, 1, 4]
    assert ids(c.post("/api/move", json={"id": 4, "to": 0}).get_json()) == [4, 2, 3, 1]
    assert ids(c.post("/api/move", json={"id": 4, "to": 99}).get_json()) == [2, 3, 1, 4]  # clamped
    assert ids(c.post("/api/undo", json={}).get_json()) == [4, 2, 3, 1]
    assert [p["id"] for p in Session(s.root).pages] == [4, 2, 3, 1]  # persisted
    # export follows the new order
    from flipscan.export import export_pdfs
    assert len(export_pdfs(Session(s.root))) == 1


def _run_batch(tmp_path, monkeypatch, scans, answers):
    import builtins
    from flipscan import cli, scan
    cfg = Config(scanner="x", sessions_dir=str(tmp_path / "s"))
    s = Session.create(cfg.sessions_path)
    counts = iter(scans)
    calls = []

    def fake_scan(cfg_, outdir):
        n = next(counts)
        calls.append(n)
        files = []
        for i in range(n):
            f = outdir / f"p_{i}.jpg"
            make_img(f, text=False)
            files.append(f)
        return files

    monkeypatch.setattr(scan, "scan_adf_fronts", fake_scan)
    monkeypatch.setattr(scan, "scanner_status", lambda cfg_: {"state": "Idle", "adf": "ScannerAdfLoaded"})
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *_: next(it))
    cli._adf_batch(cfg, s, scan)
    return s, calls


def test_mismatch_rescan_fronts_keeps_backs(tmp_path, monkeypatch):
    # fronts 4, backs 5 (mismatch) -> rescan fronts only: 5 -> pair complete, backs NOT rescanned
    s, calls = _run_batch(tmp_path, monkeypatch, [4, 5, 5], ["", "f", ""])
    assert calls == [4, 5, 5]
    assert len(s.pages) == 10


def test_mismatch_rescan_backs_keeps_fronts(tmp_path, monkeypatch):
    s, calls = _run_batch(tmp_path, monkeypatch, [5, 4, 5], ["", "r", ""])
    assert calls == [5, 4, 5] and len(s.pages) == 10


def test_mismatch_can_alternate_until_it_matches(tmp_path, monkeypatch):
    # 4/5 -> rescan fronts (3, still wrong) -> rescan backs (3) -> match
    s, calls = _run_batch(tmp_path, monkeypatch, [4, 5, 3, 3], ["", "f", "", "r", ""])
    assert calls == [4, 5, 3, 3] and len(s.pages) == 6


def test_mismatch_discard_and_keep(tmp_path, monkeypatch):
    s, calls = _run_batch(tmp_path / "a", monkeypatch, [4, 5], ["", "d"])
    assert not s.pages
    s, calls = _run_batch(tmp_path / "b", monkeypatch, [4, 5], ["", "k"])
    assert len(s.pages) == 9
