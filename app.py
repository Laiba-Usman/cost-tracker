"""Cost Tracker: estimated vs actual project costs, charts, AI analysis, reports.

Run:  python app.py      then open http://127.0.0.1:5000
"""
import collections
import hmac
import logging
import math
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import ai
import calc
import compare
import importer
import pert
import report

BASE = Path(__file__).resolve().parent
INSTANCE = BASE / "instance"
INSTANCE.mkdir(exist_ok=True)
DB_PATH = os.environ.get("COST_DB", str(INSTANCE / "costs.db"))


def _secret():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    f = INSTANCE / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text().strip()


app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.update(SECRET_KEY=_secret(), MAX_CONTENT_LENGTH=importer.MAX_BYTES + 4096,
                  SESSION_COOKIE_HTTPONLY=True, PERMANENT_SESSION_LIFETIME=timedelta(hours=8), SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
                  JSON_SORT_KEYS=False)


# ---------- abuse protection and security log ----------
API_LIMIT = int(os.environ.get("API_LIMIT_PER_MIN", 240))        # requests per minute per client address
AUTH_LIMIT = int(os.environ.get("AUTH_LIMIT_PER_10MIN", 30))     # login/register attempts per 10 min per address
LOGIN_FAILS = int(os.environ.get("LOGIN_FAILS", 5))              # wrong passwords before a temporary block
LOGIN_BLOCK_SECONDS = int(os.environ.get("LOGIN_BLOCK_SECONDS", 900))
REPORT_LIMIT = int(os.environ.get("REPORT_LIMIT_PER_MIN", 10))   # PDF/Excel builds are the most expensive calls
UPLOAD_LIMIT = int(os.environ.get("UPLOAD_LIMIT_PER_10MIN", 20))


class Limiter:
    """Sliding-window counter kept in memory (one process). Good for a prototype; use Redis for several workers."""

    def __init__(self):
        self.hits = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def _prune(self, q, window, now_):
        while q and q[0] <= now_ - window:
            q.popleft()

    def hit(self, key, limit, window):
        """Count one request. Returns (allowed, seconds_to_wait)."""
        with self.lock:
            n = time.time()
            if len(self.hits) > 20000:
                self.hits.clear()
            q = self.hits[key]
            self._prune(q, window, n)
            if len(q) >= limit:
                return False, max(1, int(q[0] + window - n) + 1)
            q.append(n)
            return True, 0

    def blocked(self, key, limit, window):
        with self.lock:
            q = self.hits.get(key)
            if not q:
                return 0
            n = time.time()
            self._prune(q, window, n)
            return max(1, int(q[0] + window - n) + 1) if len(q) >= limit else 0

    def record(self, key):
        with self.lock:
            self.hits[key].append(time.time())

    def clear(self, key):
        with self.lock:
            self.hits.pop(key, None)


limiter = Limiter()
seclog_handler = logging.FileHandler(INSTANCE / "security.log")
seclog_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_seclog = logging.getLogger("security")
_seclog.setLevel(logging.INFO)
_seclog.addHandler(seclog_handler)
_seclog.addHandler(logging.StreamHandler())   # also to the console so hosting dashboards (Render Logs) show it
if os.environ.get("TRUST_PROXY") == "1":      # only behind your own reverse proxy
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)


def client_ip():
    return request.remote_addr or "unknown"


def seclog(event, **kw):
    """One line per security event. Never log passwords or file contents."""
    extra = " ".join("%s=%s" % (k, str(v).replace("\n", " ")[:80]) for k, v in kw.items())
    _seclog.info("%s ip=%s user=%s %s", event, client_ip(), session.get("uid", "-"), extra)


def too_many(retry):
    r = jsonify({"error": "Too many requests. Try again in %d seconds." % retry})
    r.status_code = 429
    r.headers["Retry-After"] = str(retry)
    return r

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'Rs.', contingency_pct REAL NOT NULL DEFAULT 10, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  category TEXT NOT NULL, item TEXT NOT NULL, cost_type TEXT NOT NULL, estimated REAL NOT NULL, actual REAL, position INTEGER NOT NULL DEFAULT 0,
  optimistic REAL, pessimistic REAL);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript(SCHEMA)
        # Migrate: add PERT columns if they don't exist yet
        cols = [r[1] for r in c.execute("PRAGMA table_info(items)").fetchall()]
        if "optimistic" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN optimistic REAL")
        if "pessimistic" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN pessimistic REAL")


init_db()


# ---------- helpers ----------
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def err(msg, code=400):
    return jsonify({"error": msg}), code


def csrf_ok():
    tok = session.get("csrf")
    sent = request.headers.get("X-CSRF-Token", "")
    return bool(tok) and hmac.compare_digest(tok, sent)


@app.before_request
def guard():
    if request.path.startswith("/api/"):
        ok, retry = limiter.hit("api:" + client_ip(), API_LIMIT, 60)
        if not ok:
            seclog("rate_limited", path=request.path)
            return too_many(retry)
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/") and not csrf_ok():
        seclog("csrf_rejected", path=request.path)
        return err("Session expired. Reload the page and try again.", 403)


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; "
                                               "style-src 'self'; script-src 'self'; frame-ancestors 'none'")
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def login_required(f):
    from functools import wraps

    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return err("Sign in first.", 401)
        return f(*a, **k)
    return w


def get_project(pid):
    """Return the project only if it belongs to the signed-in user (404 otherwise)."""
    row = db().execute("SELECT * FROM projects WHERE id=? AND owner_id=?", (pid, session["uid"])).fetchone()
    if not row:
        seclog("access_denied", project=pid)
        abort(404)
    return dict(row)


@app.errorhandler(404)
def nf(_):
    return err("Not found.", 404)


@app.errorhandler(500)
def server_error(e):
    seclog("server_error", error=type(e).__name__)
    return err("Something went wrong on the server.", 500)


@app.errorhandler(413)
def too_big(_):
    return err("The file is larger than 2 MB.", 413)


def num(v, name, required=True, maximum=1e10):
    if v in (None, ""):
        if required:
            raise ValueError("%s is required." % name)
        return None
    if isinstance(v, bool):
        raise ValueError("%s must be a number." % name)
    try:
        f = float(str(v).replace(",", "")) if not isinstance(v, (int, float)) else float(v)
    except ValueError:
        raise ValueError("%s must be a number." % name)
    if not math.isfinite(f) or f < 0 or f > maximum:
        raise ValueError("%s must be between 0 and %s." % (name, format(int(maximum), ",d")))
    return f


def text(v, name, limit, required=True):
    s = " ".join(str(v or "").split())
    if required and not s:
        raise ValueError("%s is required." % name)
    if len(s) > limit:
        raise ValueError("%s must be at most %d characters." % (name, limit))
    return s


def project_payload(p):
    return {"id": p["id"], "name": p["name"], "currency": p["currency"], "contingency_pct": p["contingency_pct"]}


def analysis_for(p):
    rows = db().execute("SELECT id,category,item,cost_type,estimated,actual,optimistic,pessimistic FROM items WHERE project_id=? ORDER BY position,id", (p["id"],)).fetchall()
    return calc.compute([dict(r) for r in rows], p["contingency_pct"])


def add_items(pid, items):
    c = db()
    pos = c.execute("SELECT COALESCE(MAX(position),0) FROM items WHERE project_id=?", (pid,)).fetchone()[0]
    for i, it in enumerate(items, start=1):
        c.execute("INSERT INTO items(project_id,category,item,cost_type,estimated,actual,position,optimistic,pessimistic) VALUES(?,?,?,?,?,?,?,?,?)",
                  (pid, it["category"], it["item"], it["cost_type"], it["estimated"], it["actual"], pos + i, it.get("optimistic"), it.get("pessimistic")))
    c.commit()


def apply_actuals(pid, parsed):
    """Match uploaded actuals to existing items by name; unknown names become 'Unbudgeted' items."""
    c = db()
    existing = {r["item"].strip().lower(): r["id"] for r in c.execute("SELECT id,item FROM items WHERE project_id=?", (pid,))}
    updated, added = 0, []
    for it in parsed:
        if it["actual"] is None:
            continue
        key = it["item"].strip().lower()
        if key in existing:
            c.execute("UPDATE items SET actual=? WHERE id=?", (it["actual"], existing[key]))
            updated += 1
        else:
            added.append({"category": it["category"] if it["category"] != "Uncategorised" else "Unbudgeted",
                          "item": it["item"], "cost_type": it["cost_type"], "estimated": 0.0, "actual": it["actual"]})
    c.commit()
    if added:
        add_items(pid, added)
    return updated, len(added)


# ---------- pages ----------
@app.route("/")
def index():
    return send_from_directory(BASE / "templates", "index.html")


@app.route("/healthz")
def healthz():
    return "ok", 200, {"Content-Type": "text/plain"}


@app.route("/sample/<name>")
def sample(name):
    if name not in ("LYKOS_Project_Budget.xlsx", "LYKOS_Actuals_Sample.csv", "flat_template.csv"):
        abort(404)
    return send_from_directory(BASE / "sample_data", name, as_attachment=True)


# ---------- auth ----------
@app.get("/api/me")
def me():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    u = None
    if session.get("uid"):
        u = {"username": session["uname"]}
    return jsonify({"user": u, "csrf": session["csrf"]})


def _credentials():
    d = request.get_json(silent=True) or {}
    u = str(d.get("username", "")).strip()
    p = str(d.get("password", ""))
    return u, p


def auth_throttle():
    ok, retry = limiter.hit("auth:" + client_ip(), AUTH_LIMIT, 600)
    if not ok:
        seclog("auth_rate_limited")
    return None if ok else too_many(retry)


DUMMY_HASH = generate_password_hash("not-a-real-password")


@app.post("/api/register")
def register():
    if os.environ.get("ALLOW_REGISTRATION", "1") != "1":
        return err("New accounts are switched off on this server.", 403)
    blocked = auth_throttle()
    if blocked:
        return blocked
    u, p = _credentials()
    if not (3 <= len(u) <= 30) or not all(ch.isalnum() or ch in "._-" for ch in u):
        return err("Username must be 3 to 30 characters: letters, numbers, dot, dash or underscore.")
    if not (8 <= len(p) <= 128):
        return err("Password must be 8 to 128 characters.")
    try:
        cur = db().execute("INSERT INTO users(username,pw_hash,created) VALUES(?,?,?)", (u, generate_password_hash(p), now()))
        db().commit()
    except sqlite3.IntegrityError:
        return err("That username is taken.", 409)
    csrf = secrets.token_hex(16)
    session.clear()
    session.update(uid=cur.lastrowid, uname=u, csrf=csrf)
    session.permanent = True
    return jsonify({"user": {"username": u}, "csrf": csrf})


@app.post("/api/login")
def login():
    blocked = auth_throttle()
    if blocked:
        return blocked
    u, p = _credentials()
    fail_key = "fail:%s:%s" % (client_ip(), u.lower()[:40])
    wait = limiter.blocked(fail_key, LOGIN_FAILS, LOGIN_BLOCK_SECONDS)
    if wait:
        seclog("login_blocked", username=u[:30])
        return too_many(wait)
    row = db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    # always run one hash check so a wrong username and a wrong password take the same time
    good = check_password_hash(row["pw_hash"] if row else DUMMY_HASH, p)
    if not row or not good:
        limiter.record(fail_key)
        seclog("login_failed", username=u[:30])
        return err("Wrong username or password.", 401)
    limiter.clear(fail_key)
    csrf = secrets.token_hex(16)
    session.clear()
    session.update(uid=row["id"], uname=row["username"], csrf=csrf)
    session.permanent = True
    return jsonify({"user": {"username": row["username"]}, "csrf": csrf})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# ---------- projects ----------
@app.get("/api/projects")
@login_required
def list_projects():
    rows = db().execute("SELECT * FROM projects WHERE owner_id=? ORDER BY id", (session["uid"],)).fetchall()
    return jsonify([project_payload(dict(r)) for r in rows])


def _project_fields(d):
    return (text(d.get("name"), "Project name", 80), text(d.get("currency") or "Rs.", "Currency", 8),
            num(d.get("contingency_pct", 10), "Contingency", maximum=100))


@app.post("/api/projects")
@login_required
def create_project():
    try:
        name, cur, cont = _project_fields(request.get_json(silent=True) or {})
    except ValueError as e:
        return err(str(e))
    c = db().execute("INSERT INTO projects(owner_id,name,currency,contingency_pct,created) VALUES(?,?,?,?,?)",
                     (session["uid"], name, cur, cont, now()))
    db().commit()
    return jsonify(project_payload(get_project(c.lastrowid))), 201


@app.put("/api/projects/<int:pid>")
@login_required
def update_project(pid):
    get_project(pid)
    try:
        name, cur, cont = _project_fields(request.get_json(silent=True) or {})
    except ValueError as e:
        return err(str(e))
    db().execute("UPDATE projects SET name=?,currency=?,contingency_pct=? WHERE id=?", (name, cur, cont, pid))
    db().commit()
    return jsonify(project_payload(get_project(pid)))


@app.delete("/api/projects/<int:pid>")
@login_required
def delete_project(pid):
    get_project(pid)
    db().execute("DELETE FROM projects WHERE id=?", (pid,))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/demo")
@login_required
def demo():
    """Create the LYKOS example project from the bundled Excel file plus sample actual costs."""
    c = db().execute("INSERT INTO projects(owner_id,name,currency,contingency_pct,created) VALUES(?,?,?,?,?)",
                     (session["uid"], "LYKOS FYP budget (demo)", "Rs.", 10, now()))
    pid = c.lastrowid
    db().commit()
    xl = (BASE / "sample_data" / "LYKOS_Project_Budget.xlsx")
    items, _ = importer.parse_file(xl.name, xl.read_bytes())
    add_items(pid, items)
    cs = (BASE / "sample_data" / "LYKOS_Actuals_Sample.csv")
    parsed, _ = importer.parse_file(cs.name, cs.read_bytes(), need_estimate=False)
    apply_actuals(pid, parsed)
    return jsonify(project_payload(get_project(pid))), 201


# ---------- analysis ----------
@app.get("/api/projects/<int:pid>/analysis")
@login_required
def analysis(pid):
    p = get_project(pid)
    r = analysis_for(p)
    return jsonify({"project": project_payload(p), "rows": r["rows"], "summary": r["summary"],
                    "categories": r["categories"], "cost_types": r["cost_types"], "charts": calc.chart_data(r)})


@app.get("/api/projects/<int:pid>/ai")
@login_required
def ai_analysis(pid):
    p = get_project(pid)
    return jsonify(ai.analyse(p, analysis_for(p)))


# ---------- items ----------
def _item_fields(d):
    cost_type = d.get("cost_type") if d.get("cost_type") in calc.COST_TYPES else "Other"
    return {"category": text(d.get("category"), "Category", 60), "item": text(d.get("item"), "Item", importer.MAX_TEXT),
            "cost_type": cost_type, "estimated": num(d.get("estimated"), "Estimated cost"),
            "actual": num(d.get("actual"), "Actual cost", required=False),
            "optimistic": num(d.get("optimistic"), "Optimistic cost", required=False),
            "pessimistic": num(d.get("pessimistic"), "Pessimistic cost", required=False)}


@app.post("/api/projects/<int:pid>/items")
@login_required
def add_item(pid):
    get_project(pid)
    n = db().execute("SELECT COUNT(*) FROM items WHERE project_id=?", (pid,)).fetchone()[0]
    if n >= importer.MAX_ROWS:
        return err("A project can hold at most %d items." % importer.MAX_ROWS)
    try:
        it = _item_fields(request.get_json(silent=True) or {})
    except ValueError as e:
        return err(str(e))
    add_items(pid, [it])
    return jsonify({"ok": True}), 201


@app.put("/api/projects/<int:pid>/items/<int:iid>")
@login_required
def update_item(pid, iid):
    get_project(pid)
    if not db().execute("SELECT 1 FROM items WHERE id=? AND project_id=?", (iid, pid)).fetchone():
        abort(404)
    try:
        it = _item_fields(request.get_json(silent=True) or {})
    except ValueError as e:
        return err(str(e))
    db().execute("UPDATE items SET category=?,item=?,cost_type=?,estimated=?,actual=?,optimistic=?,pessimistic=? WHERE id=? AND project_id=?",
                 (it["category"], it["item"], it["cost_type"], it["estimated"], it["actual"], it["optimistic"], it["pessimistic"], iid, pid))
    db().commit()
    return jsonify({"ok": True})


@app.delete("/api/projects/<int:pid>/items/<int:iid>")
@login_required
def delete_item(pid, iid):
    get_project(pid)
    cur = db().execute("DELETE FROM items WHERE id=? AND project_id=?", (iid, pid))
    db().commit()
    if cur.rowcount == 0:
        abort(404)
    return jsonify({"ok": True})


# ---------- import ----------
@app.post("/api/projects/<int:pid>/import")
@login_required
def import_file(pid):
    get_project(pid)
    ok, retry = limiter.hit("up:%s" % session["uid"], UPLOAD_LIMIT, 600)
    if not ok:
        seclog("upload_rate_limited")
        return too_many(retry)
    f = request.files.get("file")
    mode = request.form.get("mode", "append")
    if not f or not f.filename:
        return err("Choose a file first.")
    if mode not in ("append", "replace", "actuals"):
        return err("Unknown import mode.")
    try:
        parsed, warnings = importer.parse_file(f.filename, f.read(), need_estimate=(mode != "actuals"))
    except importer.ImportError_ as e:
        seclog("upload_rejected", filename=f.filename, reason=str(e))
        return err(str(e))
    if mode == "actuals":
        updated, added = apply_actuals(pid, parsed)
        return jsonify({"message": "Updated actual cost for %d item(s); added %d unbudgeted item(s)." % (updated, added), "warnings": warnings})
    if mode == "replace":
        db().execute("DELETE FROM items WHERE project_id=?", (pid,))
        db().commit()
    add_items(pid, parsed)
    return jsonify({"message": "Imported %d item(s)." % len(parsed), "warnings": warnings})


# ---------- reports ----------
def _safe_name(p):
    return "".join(ch if ch.isalnum() else "_" for ch in p["name"])[:40] or "project"


@app.get("/api/projects/<int:pid>/report.pdf")
@login_required
def report_pdf(pid):
    p = get_project(pid)
    ok, retry = limiter.hit("rep:%s" % session["uid"], REPORT_LIMIT, 60)
    if not ok:
        return too_many(retry)
    r = analysis_for(p)
    data = report.build_pdf(p, r, ai.analyse(p, r))
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="cost_report_%s.pdf"' % _safe_name(p)})


@app.get("/api/projects/<int:pid>/report.xlsx")
@login_required
def report_xlsx(pid):
    p = get_project(pid)
    ok, retry = limiter.hit("rep:%s" % session["uid"], REPORT_LIMIT, 60)
    if not ok:
        return too_many(retry)
    data = report.build_xlsx(p, analysis_for(p))
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="cost_report_%s.xlsx"' % _safe_name(p)})


# ---------- PERT analysis ----------
@app.get("/api/projects/<int:pid>/pert")
@login_required
def pert_analysis(pid):
    p = get_project(pid)
    rows = db().execute("SELECT id,category,item,cost_type,estimated,actual,optimistic,pessimistic FROM items WHERE project_id=? ORDER BY position,id", (p["id"],)).fetchall()
    items = [dict(r) for r in rows]
    result = pert.compute_pert(items)
    return jsonify({"project": project_payload(p), **result})


@app.put("/api/projects/<int:pid>/items/<int:iid>/pert")
@login_required
def update_item_pert(pid, iid):
    get_project(pid)
    if not db().execute("SELECT 1 FROM items WHERE id=? AND project_id=?", (iid, pid)).fetchone():
        abort(404)
    d = request.get_json(silent=True) or {}
    try:
        optimistic = num(d.get("optimistic"), "Optimistic cost", required=False)
        pessimistic = num(d.get("pessimistic"), "Pessimistic cost", required=False)
    except ValueError as e:
        return err(str(e))
    db().execute("UPDATE items SET optimistic=?,pessimistic=? WHERE id=? AND project_id=?",
                 (optimistic, pessimistic, iid, pid))
    db().commit()
    return jsonify({"ok": True})


# ---------- file comparison ----------
@app.post("/api/compare")
@login_required
def compare_files_api():
    ok, retry = limiter.hit("up:%s" % session["uid"], UPLOAD_LIMIT, 600)
    if not ok:
        seclog("upload_rate_limited")
        return too_many(retry)
    raw_files = request.files.getlist("files")
    files = [f for f in raw_files if f and f.filename]
    if len(files) < 2:
        return err("Please select at least 2 files to compare.")
    if len(files) > 3:
        return err("At most 3 files can be compared at once.")
    file_data = []
    for f in files:
        file_data.append((f.filename, f.read()))
    try:
        parsed = compare.parse_uploaded_files(file_data)
        result = compare.compare_files(parsed)
    except (importer.ImportError_, ValueError) as e:
        return err(str(e))
    # Include warnings from each file
    result["warnings"] = {pf["name"]: pf["warnings"] for pf in parsed}
    return jsonify(result)


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
