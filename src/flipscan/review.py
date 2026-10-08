"""Local review web app (localhost only)."""
from flask import Flask, jsonify, request, send_from_directory, abort

from .config import Config
from .export import export_pdfs
from .session import Session
from .upload import upload_session


def create_app(cfg: Config, session: Session) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    def state():
        return {"name": session.name, "pages": session.pages,
                "documents": [[p["id"] for p in d] for d in session.documents()]}

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/session")
    def get_session():
        return jsonify(state())

    @app.post("/api/update")
    def update():
        body = request.get_json(force=True)
        fields = {k: body[k] for k in ("deleted", "cut_before", "rotation", "doc_name") if k in body}
        session.update([int(i) for i in body.get("ids", [])], **fields)
        return jsonify(state())

    @app.post("/api/undo")
    def undo():
        session.undo()
        return jsonify(state())

    @app.post("/api/export")
    def export():
        pdfs = export_pdfs(session)
        ok, failed = [], []
        if request.get_json(force=True).get("upload"):
            ok, failed = upload_session(cfg, session)
        return jsonify({"exported": [p.name for p in pdfs],
                        "uploaded": [p.name for p in ok],
                        "failed": [p.name for p in failed]})

    @app.get("/pages/<name>")
    def page(name):
        return send_from_directory(session.root / "pages", name)

    @app.get("/thumbs/<name>")
    def thumb(name):
        return send_from_directory(session.root / "thumbs", name)

    return app
