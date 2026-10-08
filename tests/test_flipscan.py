from PIL import Image, ImageDraw

from flipscan.config import Config, Upload
from flipscan.export import export_pdfs
from flipscan.review import create_app
from flipscan.scan import add_adf_batch
from flipscan.session import Session
from flipscan.upload import upload_session


def make_img(path, text=True):
    img = Image.new("RGB", (600, 800), "white")
    if text:
        d = ImageDraw.Draw(img)
        for y in range(200, 600, 20):
            d.rectangle((100, y, 500, y + 8), fill="black")
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
    blanks = [p for p in s.pages if p["blank_suggested"]]
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
