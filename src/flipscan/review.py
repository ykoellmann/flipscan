"""Local review web app (localhost only). Serves all sessions; one is selected
at a time."""
from flask import Flask, abort, jsonify, request, send_from_directory

from .config import Config
from .export import export_pdfs
from .session import Session, delete_session
from .upload import upload_session


def create_app(cfg: Config, session: Session) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    session.ensure_ink()
    cache = {session.name: session}
    current = {"name": session.name}
    threshold = 1 - cfg.blank_threshold  # max share of content for a "blank" suggestion

    def get(name: str) -> Session | None:
        if name not in cache:
            s = Session.open_named(cfg.sessions_path, name)
            if s is None:
                return None
            s.ensure_ink()
            cache[name] = s
        return cache[name]

    def cur() -> Session:
        if current["name"] is None:
            abort(409)
        return cache[current["name"]]

    def state():
        if current["name"] is None:  # last session was deleted
            return {"name": None, "status": "open", "pages": [], "documents": []}
        s = cur()
        pages = [{**p, "blank_suggested": p.get("ink", 1) <= threshold} for p in s.pages]
        return {"name": s.name, "status": s.status, "pages": pages,
                "documents": [[p["id"] for p in d] for d in s.documents()]}

    def summary(s: Session) -> dict:
        kept = sum(not p["deleted"] for p in s.pages)
        return {"name": s.name, "status": s.status, "pages": len(s.pages),
                "kept": kept, "documents": len(s.documents()),
                "created": s.data.get("created"), "current": s.name == current["name"]}

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/sessions")
    def sessions():
        names = {p.parent.name for p in cfg.sessions_path.glob("*/session.json")} | set(cache)
        found = [get(n) for n in sorted(names, reverse=True)]
        return jsonify([summary(s) for s in found if s and s.pages])  # hide empty sessions

    @app.delete("/api/sessions/<name>")
    def delete(name):
        if not delete_session(cfg.sessions_path, name):
            return jsonify({"error": "unknown session"}), 404
        cache.pop(name, None)
        if current["name"] == name:
            rest = sorted((p.parent.name for p in cfg.sessions_path.glob("*/session.json")), reverse=True)
            current["name"] = next((n for n in rest if get(n) and get(n).pages), rest[0] if rest else None)
            if current["name"]:
                get(current["name"])
        return jsonify(state())

    @app.post("/api/select")
    def select():
        name = request.get_json(force=True).get("name", "")
        if get(name) is None:
            return jsonify({"error": "unknown session"}), 404
        current["name"] = name
        return jsonify(state())

    @app.post("/api/status")
    def status():
        st = request.get_json(force=True).get("status")
        if st not in ("open", "done"):
            return jsonify({"error": "bad status"}), 400
        cur().set_status(st)
        return jsonify(state())

    @app.get("/api/session")
    def get_session():
        return jsonify(state())

    @app.post("/api/update")
    def update():
        body = request.get_json(force=True)
        fields = {k: body[k] for k in ("deleted", "cut_before", "rotation", "doc_name") if k in body}
        cur().update([int(i) for i in body.get("ids", [])],
                     rotate_by=int(body.get("rotate_by", 0)), **fields)
        return jsonify(state())

    @app.post("/api/move")
    def move():
        body = request.get_json(force=True)
        cur().move(int(body["id"]), int(body["to"]))
        return jsonify(state())

    @app.post("/api/undo")
    def undo():
        cur().undo()
        return jsonify(state())

    @app.post("/api/export")
    def export():
        s = cur()
        pdfs = export_pdfs(s)
        ok, failed = [], []
        if request.get_json(force=True).get("upload"):
            ok, failed = upload_session(cfg, s)
        return jsonify({"exported": [p.name for p in pdfs],
                        "uploaded": [p.name for p in ok],
                        "failed": [p.name for p in failed],
                        "status": s.status})

    @app.get("/pages/<name>")
    def page(name):
        return send_from_directory(cur().root / "pages", name)

    @app.get("/thumbs/<name>")
    def thumb(name):
        return send_from_directory(cur().root / "thumbs", name)

    return app
