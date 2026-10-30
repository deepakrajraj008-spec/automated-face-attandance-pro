"""Face Attendance Pro: logins, admin page, live face recognition, entry/exit times, leave.

Run on your computer:
    python -m pip install -r requirements.txt
    python app.py
Open http://127.0.0.1:5000 (first run asks you to create the admin account).

The camera runs in the browser, so the same app works on your PC and online.
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import string
import tempfile
import threading
import time
import zlib
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from flask import Flask, Response, abort, g, jsonify, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "attendance.db")
SHOTS = 40            # photos taken per enrolment run
MAX_PHOTOS = 120      # per student
MAX_FRAME = 400 * 1024

# What an admin can switch on or off for each user
PERMS = {
    "take_attendance": "Run the live attendance camera",
    "self_checkin": "Check in and out with their own face",
    "view_own": "See their own attendance",
    "apply_leave": "Apply for leave",
    "view_reports": "See everyone's attendance and reports",
    "edit_attendance": "Add or correct attendance records",
    "manage_leaves": "Approve or reject leave",
    "manage_students": "Add, edit and delete students and their photos",
    "manage_settings": "Change settings and holidays",
}
ROLE_PRESETS = {
    "admin": list(PERMS),
    "teacher": ["take_attendance", "view_reports", "edit_attendance", "manage_leaves"],
    "student": ["view_own", "apply_leave"],
}
DEFAULT_SETTINGS = {
    "org_name": "Face Attendance",
    "student_limit": 50,
    "working_days": [0, 1, 2, 3, 4, 5],   # Monday=0 ... Sunday=6
    "min_gap_minutes": 30,                # an exit is recorded only this long after entry (auto mode)
    "threshold": 70,                      # LBPH distance: lower = stricter
    "timezone": "Asia/Kolkata",
    "term_start": "",
}

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    MAX_CONTENT_LENGTH=80 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


def _secret_key():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(DATA_DIR, "secret.key")
    if os.path.exists(path):
        return open(path).read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


app.secret_key = _secret_key()

# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, roll TEXT DEFAULT '',
    branch TEXT DEFAULT '', active INTEGER DEFAULT 1, joined TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, name TEXT NOT NULL, pwd_hash TEXT NOT NULL, role TEXT NOT NULL,
    perms TEXT DEFAULT '[]', student_id INTEGER, active INTEGER DEFAULT 1, pwv INTEGER DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, jpeg BLOB NOT NULL,
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS photos_student ON photos(student_id);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, date TEXT NOT NULL,
    entry_time TEXT, exit_time TEXT, source TEXT DEFAULT 'face', updated_at REAL NOT NULL,
    UNIQUE(student_id, date), FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL, from_date TEXT NOT NULL,
    to_date TEXT NOT NULL, reason TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
    applied_at REAL NOT NULL, decided_by TEXT, decided_at REAL, note TEXT DEFAULT '',
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS holidays (date TEXT PRIMARY KEY, label TEXT DEFAULT '');
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def get_settings(conn=None):
    conn = conn or db()
    out = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in out:
            try:
                out[row["key"]] = json.loads(row["value"])
            except ValueError:
                pass
    return out


def get_tz(settings):
    try:
        return ZoneInfo(settings["timezone"])
    except Exception:
        if settings["timezone"] == "Asia/Kolkata":
            return timezone(timedelta(hours=5, minutes=30))
        return timezone.utc


def local_now(settings):
    return datetime.now(get_tz(settings))


# ----------------------------------------------------------------------------
# Users, sessions, permissions
# ----------------------------------------------------------------------------
USER_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
fail_log = {}   # (ip, user) -> [count, first_failure_time]


def make_password(n=10):
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def current_user():
    if "user" in g:
        return g.user
    g.user = None
    uid = session.get("uid")
    if uid:
        row = db().execute("SELECT * FROM users WHERE user_id=? AND active=1", (uid,)).fetchone()
        if row and row["pwv"] == session.get("pwv"):
            g.user = row
    return g.user


def perms_of(user):
    if user is None:
        return set()
    if user["role"] == "admin":
        return set(PERMS) | {"manage_users", "backup"}
    try:
        return set(json.loads(user["perms"] or "[]")) & set(PERMS)
    except ValueError:
        return set()


def need(*wanted, any_of=False):
    """Route decorator: user must be logged in and have the permission(s)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return jsonify(error="Please log in."), 401
            have = perms_of(user)
            ok = (any(p in have for p in wanted) if any_of else all(p in have for p in wanted)) if wanted else True
            if not ok:
                return jsonify(error="You do not have access to this feature."), 403
            return fn(*args, **kwargs)
        return wrapper
    return deco


@app.before_request
def csrf_guard():
    if request.method in ("GET", "HEAD", "OPTIONS") or not request.path.startswith("/api/"):
        return None
    if request.path in ("/api/login", "/api/setup"):
        if request.headers.get("X-Requested-With") != "fetch":
            return jsonify(error="Bad request."), 400
        return None
    token = session.get("csrf")
    if not token or request.headers.get("X-CSRF-Token") != token:
        return jsonify(error="Your session expired. Please log in again."), 401
    return None


def public_user(user):
    if user is None:
        return None
    return {"user_id": user["user_id"], "name": user["name"], "role": user["role"],
            "student_id": user["student_id"], "perms": sorted(perms_of(user))}


def login_session(user):
    session.clear()
    session.permanent = True
    session["uid"] = user["user_id"]
    session["pwv"] = user["pwv"]
    session["csrf"] = secrets.token_urlsafe(24)


def clean_user_id(raw):
    uid = (raw or "").strip().lower()
    return uid if USER_RE.match(uid) else None


def create_user(conn, user_id, name, role, password, perms=None, student_id=None):
    if perms is None:
        perms = ROLE_PRESETS.get(role, [])
    perms = [p for p in perms if p in PERMS]
    conn.execute("INSERT INTO users(user_id,name,pwd_hash,role,perms,student_id,created) VALUES(?,?,?,?,?,?,?)",
                 (user_id, name, generate_password_hash(password), role, json.dumps(perms), student_id, time.time()))


@app.get("/api/me")
def me():
    conn = db()
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    s = get_settings(conn)
    user = current_user()
    return jsonify(user=public_user(user), csrf=session.get("csrf") if user else None,
                   needs_setup=users == 0, cloud_state=cloud["state"], org_name=s["org_name"], perms_info=PERMS if user else None,
                   presets=ROLE_PRESETS if user else None)


@app.post("/api/setup")
def setup():
    conn = db()
    if cloud["state"] in ("starting", "failed"):
        return jsonify(error="The saved cloud copy could not be reached yet. Wait a minute and reload this page."), 503
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return jsonify(error="Setup is already done."), 400
    body = request.get_json(silent=True) or {}
    uid, name, pwd = clean_user_id(body.get("user_id")), (body.get("name") or "").strip()[:60], body.get("password") or ""
    if not uid:
        return jsonify(error="User ID: 3 to 32 letters, numbers, dot, dash or underscore."), 400
    if len(pwd) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    create_user(conn, uid, name or "Administrator", "admin", pwd)
    org = (body.get("org_name") or "").strip()[:60]
    if org:
        conn.execute("INSERT OR REPLACE INTO settings VALUES('org_name',?)", (json.dumps(org),))
    conn.commit()
    login_session(conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    uid, pwd = (body.get("user_id") or "").strip().lower(), body.get("password") or ""
    key = (request.remote_addr, uid)
    count, first = fail_log.get(key, [0, time.time()])
    if count >= 5 and time.time() - first < 300:
        return jsonify(error="Too many wrong attempts. Wait 5 minutes and try again."), 429
    if time.time() - first >= 300:
        count, first = 0, time.time()
    row = db().execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row or not row["active"] or not check_password_hash(row["pwd_hash"], pwd):
        fail_log[key] = [count + 1, first]
        return jsonify(error="Wrong User ID or password."), 401
    fail_log.pop(key, None)
    login_session(row)
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/change-password")
@need()
def change_password():
    body = request.get_json(silent=True) or {}
    user = current_user()
    if not check_password_hash(user["pwd_hash"], body.get("old") or ""):
        return jsonify(error="Your current password is wrong."), 400
    new = body.get("new") or ""
    if len(new) < 6:
        return jsonify(error="New password must be at least 6 characters."), 400
    conn = db()
    conn.execute("UPDATE users SET pwd_hash=?, pwv=pwv+1 WHERE user_id=?", (generate_password_hash(new), user["user_id"]))
    conn.commit()
    login_session(conn.execute("SELECT * FROM users WHERE user_id=?", (user["user_id"],)).fetchone())
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Admin: users and access
# ----------------------------------------------------------------------------
def user_row_json(r):
    try:
        perms = json.loads(r["perms"] or "[]")
    except ValueError:
        perms = []
    return {"user_id": r["user_id"], "name": r["name"], "role": r["role"], "perms": perms,
            "student_id": r["student_id"], "active": bool(r["active"])}


def active_admins(conn, excluding=None):
    q = "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
    args = ()
    if excluding:
        q += " AND user_id<>?"
        args = (excluding,)
    return conn.execute(q, args).fetchone()[0]


@app.get("/api/users")
@need("manage_users")
def users_list():
    rows = db().execute("SELECT u.*, s.name AS student_name, s.roll AS roll FROM users u "
                        "LEFT JOIN students s ON s.id=u.student_id ORDER BY u.role, u.user_id").fetchall()
    out = []
    for r in rows:
        item = user_row_json(r)
        item["student_name"], item["roll"] = r["student_name"], r["roll"]
        out.append(item)
    return jsonify(out)


@app.post("/api/users")
@need("manage_users")
def users_create():
    body = request.get_json(silent=True) or {}
    conn = db()
    uid = clean_user_id(body.get("user_id"))
    name = (body.get("name") or "").strip()[:60]
    role = body.get("role") if body.get("role") in ROLE_PRESETS else "teacher"
    if not uid:
        return jsonify(error="User ID: 3 to 32 letters, numbers, dot, dash or underscore."), 400
    if not name:
        return jsonify(error="Enter the person's name."), 400
    if conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
        return jsonify(error="That User ID is already taken."), 400
    sid = body.get("student_id")
    if role == "student":
        if not sid or not conn.execute("SELECT 1 FROM students WHERE id=?", (sid,)).fetchone():
            return jsonify(error="Pick the student this login belongs to."), 400
        if conn.execute("SELECT 1 FROM users WHERE student_id=?", (sid,)).fetchone():
            return jsonify(error="That student already has a login."), 400
    else:
        sid = None
    password = body.get("password") or make_password()
    if len(password) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    perms = body.get("perms") if isinstance(body.get("perms"), list) else ROLE_PRESETS[role]
    create_user(conn, uid, name, role, password, perms, sid)
    conn.commit()
    return jsonify(ok=True, user_id=uid, password=password)


@app.put("/api/users/<uid>")
@need("manage_users")
def users_update(uid):
    body = request.get_json(silent=True) or {}
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return jsonify(error="User not found."), 404
    me_ = current_user()
    name = (body.get("name") or row["name"]).strip()[:60]
    role = body.get("role") if body.get("role") in ROLE_PRESETS else row["role"]
    active = int(bool(body.get("active", row["active"])))
    perms = body.get("perms") if isinstance(body.get("perms"), list) else json.loads(row["perms"] or "[]")
    perms = [p for p in perms if p in PERMS]
    if row["role"] == "admin" and (role != "admin" or not active) and active_admins(conn, uid) == 0:
        return jsonify(error="There must always be at least one active admin."), 400
    if uid == me_["user_id"] and (not active or role != row["role"]):
        return jsonify(error="You cannot lock or demote your own account."), 400
    sid = row["student_id"]
    if role == "student" and "student_id" in body:
        sid = body["student_id"]
    if role != "student":
        sid = None
    conn.execute("UPDATE users SET name=?, role=?, perms=?, active=?, student_id=?, pwv=pwv+? WHERE user_id=?",
                 (name, role, json.dumps(perms), active, sid, 0 if active else 1, uid))
    conn.commit()
    return jsonify(ok=True)


@app.post("/api/users/<uid>/reset-password")
@need("manage_users")
def users_reset(uid):
    conn = db()
    if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
        return jsonify(error="User not found."), 404
    password = make_password()
    conn.execute("UPDATE users SET pwd_hash=?, pwv=pwv+1 WHERE user_id=?", (generate_password_hash(password), uid))
    conn.commit()
    return jsonify(ok=True, user_id=uid, password=password)


@app.delete("/api/users/<uid>")
@need("manage_users")
def users_delete(uid):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return jsonify(error="User not found."), 404
    if uid == current_user()["user_id"]:
        return jsonify(error="You cannot delete your own account."), 400
    if row["role"] == "admin" and active_admins(conn, uid) == 0:
        return jsonify(error="There must always be at least one active admin."), 400
    conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
    conn.commit()
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Settings and holidays
# ----------------------------------------------------------------------------
@app.get("/api/settings")
@need()
def settings_get():
    s = get_settings()
    s["holidays"] = [dict(r) for r in db().execute("SELECT * FROM holidays ORDER BY date")]
    s["student_count"] = db().execute("SELECT COUNT(*) FROM students").fetchone()[0]
    return jsonify(s)


def valid_date(text):
    try:
        return date.fromisoformat(text).isoformat()
    except (TypeError, ValueError):
        return None


@app.put("/api/settings")
@need("manage_settings")
def settings_put():
    body = request.get_json(silent=True) or {}
    cur, new = get_settings(), {}
    if "org_name" in body:
        new["org_name"] = (str(body["org_name"]).strip() or cur["org_name"])[:60]
    for key, low, high in (("student_limit", 1, 5000), ("min_gap_minutes", 1, 720), ("threshold", 30, 120)):
        if key in body:
            try:
                new[key] = max(low, min(high, int(body[key])))
            except (TypeError, ValueError):
                return jsonify(error=f"{key} must be a number."), 400
    if "working_days" in body:
        days = sorted({int(d) for d in body["working_days"] if str(d).isdigit() and 0 <= int(d) <= 6})
        if not days:
            return jsonify(error="Pick at least one working day."), 400
        new["working_days"] = days
    if "timezone" in body:
        try:
            ZoneInfo(body["timezone"])
            new["timezone"] = body["timezone"]
        except Exception:
            return jsonify(error="Unknown time zone. Example: Asia/Kolkata"), 400
    if "term_start" in body:
        ts = (body["term_start"] or "").strip()
        if ts and not valid_date(ts):
            return jsonify(error="Term start must be a date."), 400
        new["term_start"] = ts
    conn = db()
    for k, v in new.items():
        conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, json.dumps(v)))
    conn.commit()
    return jsonify(ok=True)


@app.post("/api/holidays")
@need("manage_settings")
def holiday_add():
    body = request.get_json(silent=True) or {}
    d = valid_date(body.get("date"))
    if not d:
        return jsonify(error="Pick a date."), 400
    db().execute("INSERT OR REPLACE INTO holidays VALUES(?,?)", (d, (body.get("label") or "").strip()[:60]))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/holidays/<d>")
@need("manage_settings")
def holiday_del(d):
    db().execute("DELETE FROM holidays WHERE date=?", (d,))
    db().commit()
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Face detection and recognition
# ----------------------------------------------------------------------------
detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
detector_alt = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
cv_lock = threading.Lock()      # OpenCV cascades must not be shared between threads
model_lock = threading.Lock()
model = None                    # LBPH recogniser trained on all active students
model_ids = set()               # student ids the model knows


def find_faces(gray):
    """Even out the lighting, then look for faces with two detectors."""
    eq = cv2.equalizeHist(gray)
    with cv_lock:
        for det, neighbours, smallest in ((detector, 4, (50, 50)), (detector_alt, 3, (40, 40))):
            boxes = det.detectMultiScale(eq, 1.1, neighbours, minSize=smallest)
            if len(boxes):
                return eq, boxes
    return eq, []


def train_model():
    """Train on every photo of every active student. Returns the number of people."""
    global model, model_ids
    conn = connect()
    rows = conn.execute("SELECT p.student_id, p.jpeg FROM photos p JOIN students s ON s.id=p.student_id "
                        "WHERE s.active=1").fetchall()
    conn.close()
    images, labels = [], []
    for r in rows:
        im = cv2.imdecode(np.frombuffer(r["jpeg"], np.uint8), cv2.IMREAD_GRAYSCALE)
        if im is not None and im.shape == (200, 200):
            images.append(im)
            labels.append(r["student_id"])
    if not images:
        with model_lock:
            model, model_ids = None, set()
        return 0
    new_model = cv2.face.LBPHFaceRecognizer_create()
    new_model.train(images, np.array(labels, dtype=np.int32))
    with model_lock:
        model, model_ids = new_model, set(labels)
    return len(set(labels))


def read_frame():
    if request.content_length and request.content_length > MAX_FRAME:
        abort(413)
    img = cv2.imdecode(np.frombuffer(request.get_data(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        abort(400)
    return img


def crop200(eq, box):
    x, y, w, h = box
    return cv2.resize(eq[y:y + h, x:x + w], (200, 200))


pending = {}   # (user, student) -> [hits, last_time]: a face must be seen twice before attendance is marked


def confirmed(user_id, sid, need_hits=2, window=6.0):
    now = time.time()
    if len(pending) > 500:
        pending.clear()
    entry = pending.get((user_id, sid))
    if entry and now - entry[1] <= window:
        entry[0] += 1
        entry[1] = now
    else:
        entry = pending[(user_id, sid)] = [1, now]
    return entry[0] >= need_hits


def mark(conn, sid, mode, settings):
    """Record entry or exit. Returns (event, time)."""
    now = local_now(settings)
    d, t = now.date().isoformat(), now.strftime("%H:%M:%S")
    row = conn.execute("SELECT * FROM attendance WHERE student_id=? AND date=?", (sid, d)).fetchone()
    if row is None or row["entry_time"] is None:
        if mode == "exit":
            return "no_entry", None
        conn.execute("INSERT OR REPLACE INTO attendance(student_id,date,entry_time,source,updated_at) VALUES(?,?,?,?,?)",
                     (sid, d, t, "face", time.time()))
        conn.commit()
        touch()
        return "entry", t
    if mode == "entry":
        return "already", row["entry_time"]
    entry_dt = datetime.strptime(d + " " + row["entry_time"], "%Y-%m-%d %H:%M:%S")
    elapsed = (now.replace(tzinfo=None) - entry_dt).total_seconds()
    gap = 60 if mode == "exit" else int(settings["min_gap_minutes"]) * 60
    if elapsed < gap:
        return "too_soon", row["entry_time"]
    if row["exit_time"] and time.time() - row["updated_at"] < 60:
        return "exit", row["exit_time"]
    conn.execute("UPDATE attendance SET exit_time=?, updated_at=? WHERE id=?", (t, time.time(), row["id"]))
    conn.commit()
    touch()
    return "exit", t


def recognise(kind):
    """Shared by the staff camera (kind='kiosk') and own-face check-in (kind='self')."""
    img = read_frame()
    started = time.time()
    mode = request.args.get("mode", "auto")
    mode = mode if mode in ("auto", "entry", "exit") else "auto"
    conn = db()
    s = get_settings(conn)
    user = current_user()
    eq, boxes = find_faces(img)
    faces, events = [], []
    own = user["student_id"] if kind == "self" else None
    for box in list(boxes)[:5]:
        x, y, w, h = [int(v) for v in box]
        f = {"x": x, "y": y, "w": w, "h": h, "name": "Unknown", "state": "bad", "conf": None}
        with model_lock:
            current = model
            known = set(model_ids)
        if current is None:
            f.update(name="Train the model first", state="warn")
        else:
            with model_lock:
                label, conf = current.predict(crop200(eq, box))
            f["conf"] = round(float(conf), 1)
            if conf < s["threshold"] and label in known:
                st = conn.execute("SELECT * FROM students WHERE id=? AND active=1", (label,)).fetchone()
                if st and kind == "self" and label != own:
                    f.update(name="Does not match your account", state="bad")
                elif st:
                    f.update(name=st["name"], roll=st["roll"], state="ok")
                    if confirmed(user["user_id"], label):
                        ev, t = mark(conn, label, mode, s)
                        events.append({"student_id": label, "name": st["name"], "roll": st["roll"], "type": ev, "time": t})
                        f["event"] = ev
            elif kind == "self":
                f.update(name="Not recognised", state="bad")
        faces.append(f)
    return jsonify(faces=faces, events=events, trained=model is not None,
                   w=int(img.shape[1]), h=int(img.shape[0]), ms=int((time.time() - started) * 1000))


@app.post("/api/recognize")
@need("take_attendance")
def recognize_route():
    return recognise("kiosk")


@app.post("/api/selfcheck")
@need("self_checkin")
def selfcheck_route():
    if not current_user()["student_id"]:
        return jsonify(error="Your login is not linked to a student."), 400
    return recognise("self")


@app.post("/api/train")
@need("manage_students")
def train_route():
    n = train_model()
    return jsonify(ok=bool(n), msg=f"Training complete ({n} students)." if n else "No photos yet. Enrol a student first.")


# ----------------------------------------------------------------------------
# Students
# ----------------------------------------------------------------------------
def student_json(r, photos=None):
    return {"id": r["id"], "name": r["name"], "roll": r["roll"], "branch": r["branch"],
            "active": bool(r["active"]), "joined": r["joined"], "photos": photos}


@app.get("/api/students")
@need("manage_students", "view_reports", "edit_attendance", "manage_leaves", "take_attendance", any_of=True)
def students_list():
    conn = db()
    counts = dict(conn.execute("SELECT student_id, COUNT(*) FROM photos GROUP BY student_id").fetchall())
    logins = dict(conn.execute("SELECT student_id, user_id FROM users WHERE student_id IS NOT NULL").fetchall())
    out = []
    for r in conn.execute("SELECT * FROM students ORDER BY name COLLATE NOCASE"):
        item = student_json(r, counts.get(r["id"], 0))
        item["login"] = logins.get(r["id"])
        out.append(item)
    s = get_settings(conn)
    return jsonify(students=out, limit=s["student_limit"], trained=model is not None)


@app.post("/api/students")
@need("manage_students")
def students_add():
    body = request.get_json(silent=True) or {}
    conn = db()
    s = get_settings(conn)
    name = (body.get("name") or "").strip()[:60]
    roll = (body.get("roll") or "").strip()[:30]
    branch = (body.get("branch") or "").strip()[:40]
    if not name:
        return jsonify(error="Enter the student's name."), 400
    count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    if count >= s["student_limit"]:
        return jsonify(error=f"Student limit reached ({s['student_limit']}). Raise the limit in Settings or remove a student."), 400
    if roll and conn.execute("SELECT 1 FROM students WHERE roll=?", (roll,)).fetchone():
        return jsonify(error="That roll number already exists."), 400
    joined = valid_date(body.get("joined")) or local_now(s).date().isoformat()
    cur = conn.execute("INSERT INTO students(name,roll,branch,joined,created) VALUES(?,?,?,?,?)",
                       (name, roll, branch, joined, time.time()))
    sid = cur.lastrowid
    login = None
    if body.get("create_login"):
        uid = clean_user_id(body.get("user_id") or roll or f"s{sid}") or f"s{sid}"
        while conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
            uid = f"{uid[:26]}{secrets.randbelow(900) + 100}"
        pwd = make_password()
        create_user(conn, uid, name, "student", pwd, ROLE_PRESETS["student"], sid)
        login = {"user_id": uid, "password": pwd}
    conn.commit()
    return jsonify(ok=True, id=sid, login=login)


@app.put("/api/students/<int:sid>")
@need("manage_students")
def students_update(sid):
    body = request.get_json(silent=True) or {}
    conn = db()
    r = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not r:
        return jsonify(error="Student not found."), 404
    name = (body.get("name") or r["name"]).strip()[:60]
    roll = (body.get("roll", r["roll"]) or "").strip()[:30]
    branch = (body.get("branch", r["branch"]) or "").strip()[:40]
    active = int(bool(body.get("active", r["active"])))
    joined = valid_date(body.get("joined")) or r["joined"]
    if roll and conn.execute("SELECT 1 FROM students WHERE roll=? AND id<>?", (roll, sid)).fetchone():
        return jsonify(error="That roll number already exists."), 400
    conn.execute("UPDATE students SET name=?, roll=?, branch=?, active=?, joined=? WHERE id=?",
                 (name, roll, branch, active, joined, sid))
    conn.execute("UPDATE users SET name=? WHERE student_id=?", (name, sid))
    conn.commit()
    if active != r["active"]:
        train_model()
    return jsonify(ok=True)


@app.delete("/api/students/<int:sid>")
@need("manage_students")
def students_delete(sid):
    conn = db()
    conn.execute("DELETE FROM users WHERE student_id=?", (sid,))
    conn.execute("DELETE FROM students WHERE id=?", (sid,))
    conn.commit()
    train_model()
    return jsonify(ok=True)


@app.delete("/api/students/<int:sid>/photos")
@need("manage_students")
def photos_clear(sid):
    db().execute("DELETE FROM photos WHERE student_id=?", (sid,))
    db().commit()
    train_model()
    return jsonify(ok=True)


@app.post("/api/enroll/frame")
@need("manage_students")
def enroll_frame():
    img = read_frame()
    conn = db()
    try:
        sid = int(request.args.get("student", ""))
    except ValueError:
        return jsonify(error="No student chosen."), 400
    if not conn.execute("SELECT 1 FROM students WHERE id=?", (sid,)).fetchone():
        return jsonify(error="Student not found."), 404
    eq, boxes = find_faces(img)
    faces = []
    total = conn.execute("SELECT COUNT(*) FROM photos WHERE student_id=?", (sid,)).fetchone()[0]
    if len(boxes):
        box = max(boxes, key=lambda b: b[2] * b[3])
        x, y, w, h = [int(v) for v in box]
        if total < MAX_PHOTOS and request.args.get("preview") != "1":
            ok, buf = cv2.imencode(".jpg", crop200(eq, box), [cv2.IMWRITE_JPEG_QUALITY, 92])
            conn.execute("INSERT INTO photos(student_id, jpeg) VALUES(?,?)", (sid, buf.tobytes()))
            conn.commit()
            touch()
            total += 1
        faces.append({"x": x, "y": y, "w": w, "h": h, "name": "Capturing", "state": "enroll"})
    return jsonify(faces=faces, photos=total, shots=SHOTS, w=int(img.shape[1]), h=int(img.shape[0]))


# ----------------------------------------------------------------------------
# Attendance maths: the leave rule lives here
# ----------------------------------------------------------------------------
def holiday_set(conn):
    return {r["date"] for r in conn.execute("SELECT date FROM holidays")}


def approved_leave_days(conn, sid):
    days = set()
    for r in conn.execute("SELECT from_date, to_date FROM leaves WHERE student_id=? AND status='approved'", (sid,)):
        d, end = date.fromisoformat(r["from_date"]), date.fromisoformat(r["to_date"])
        while d <= end:
            days.add(d.isoformat())
            d += timedelta(days=1)
    return days


def day_status(d, att, leave_days, working, holidays, today):
    key = d.isoformat()
    is_work = d.weekday() in working and key not in holidays
    if is_work and key in leave_days:
        return "leave"
    if key in att and att[key]["entry_time"]:
        return "present"
    if d.weekday() not in working:
        return "off"
    if key in holidays:
        return "holiday"
    if d >= today:
        return "pending"
    return "absent"


def student_start(st, s):
    start = date.fromisoformat(st["joined"])
    if s["term_start"]:
        start = max(start, date.fromisoformat(s["term_start"]))
    return start


def summary(conn, st, s, holidays=None):
    """Attendance percentage.
    Approved leave days are taken out of BOTH the days attended and the days counted, so applying for
    leave never lowers the percentage and never raises it: it simply stays where it was."""
    today = local_now(s).date()
    holidays = holidays if holidays is not None else holiday_set(conn)
    working = set(s["working_days"])
    att = {r["date"]: r for r in conn.execute("SELECT * FROM attendance WHERE student_id=?", (st["id"],))}
    leaves = approved_leave_days(conn, st["id"])
    start, d = student_start(st, s), student_start(st, s)
    present = absent = on_leave = 0
    history = []
    while d <= today:
        status = day_status(d, att, leaves, working, holidays, today)
        if status == "present":
            present += 1
        elif status == "absent":
            absent += 1
        elif status == "leave":
            on_leave += 1
        row = att.get(d.isoformat())
        history.append({"date": d.isoformat(), "status": status,
                        "entry": row["entry_time"] if row else None, "exit": row["exit_time"] if row else None})
        d += timedelta(days=1)
    counted = present + absent
    percent = round(present / counted * 100, 1) if counted else None
    return {"percent": percent, "present": present, "absent": absent, "leave": on_leave, "counted_days": counted,
            "start": start.isoformat(), "today": history[-1] if history else None, "history": history[-45:][::-1]}


def duration(entry, exit_):
    if not entry or not exit_:
        return ""
    a = datetime.strptime(entry, "%H:%M:%S")
    b = datetime.strptime(exit_, "%H:%M:%S")
    secs = int((b - a).total_seconds())
    return f"{secs // 3600}h {secs % 3600 // 60:02d}m" if secs >= 0 else ""


def report_rows(conn, start, end, search="", branch="", status="", student_id=None):
    s = get_settings(conn)
    today = local_now(s).date()
    end = min(end, today)
    holidays, working = holiday_set(conn), set(s["working_days"])
    q, args = "SELECT * FROM students WHERE 1=1", []
    if student_id:
        q, args = q + " AND id=?", [student_id]
    if branch:
        q, args = q + " AND branch=?", args + [branch]
    if search:
        q, args = q + " AND (name LIKE ? OR roll LIKE ?)", args + [f"%{search}%", f"%{search}%"]
    students = conn.execute(q + " ORDER BY name COLLATE NOCASE", args).fetchall()
    rows = []
    for st in students:
        if not st["active"] and not student_id:
            continue
        att = {r["date"]: r for r in conn.execute(
            "SELECT * FROM attendance WHERE student_id=? AND date BETWEEN ? AND ?", (st["id"], start.isoformat(), end.isoformat()))}
        leaves = approved_leave_days(conn, st["id"])
        joined = date.fromisoformat(st["joined"])
        d = start
        while d <= end:
            if d < joined and d.isoformat() not in att:
                d += timedelta(days=1)   # before they joined: nothing to report, unless a record exists
                continue
            stat = day_status(d, att, leaves, working, holidays, today)
            if not status or stat == status:
                r = att.get(d.isoformat())
                rows.append({"date": d.isoformat(), "student_id": st["id"], "name": st["name"], "roll": st["roll"],
                             "branch": st["branch"], "entry": r["entry_time"] if r else "", "exit": r["exit_time"] if r else "",
                             "duration": duration(r["entry_time"], r["exit_time"]) if r else "", "status": stat})
            d += timedelta(days=1)
    rows.sort(key=lambda x: (x["date"], x["name"].lower()), reverse=True)
    return rows[:6000]


def parse_range(default_days=0):
    s = get_settings()
    today = local_now(s).date()
    single = valid_date(request.args.get("date"))
    if single:
        return date.fromisoformat(single), date.fromisoformat(single)
    end = date.fromisoformat(valid_date(request.args.get("to")) or today.isoformat())
    start = date.fromisoformat(valid_date(request.args.get("from")) or (today - timedelta(days=default_days)).isoformat())
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    return start, end


@app.get("/api/attendance")
@need("view_reports")
def attendance_list():
    start, end = parse_range(0)
    rows = report_rows(db(), start, end, request.args.get("search", "").strip(), request.args.get("branch", "").strip(),
                       request.args.get("status", "").strip())
    return jsonify(rows=rows, start=start.isoformat(), end=end.isoformat())


@app.get("/api/attendance/export.csv")
@need("view_reports")
def attendance_export():
    start, end = parse_range(30)
    rows = report_rows(db(), start, end, request.args.get("search", "").strip(), request.args.get("branch", "").strip(),
                       request.args.get("status", "").strip())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Name", "Roll Number", "Branch", "Entry", "Exit", "Duration", "Status"])

    def safe(v):
        v = str(v or "")
        return "'" + v if v[:1] in "=+-@" else v
    for r in rows:
        w.writerow([r["date"], safe(r["name"]), safe(r["roll"]), safe(r["branch"]), r["entry"], r["exit"], r["duration"], r["status"]])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=attendance.csv"})


@app.get("/api/attendance/today")
@need("take_attendance", "view_reports", any_of=True)
def attendance_today():
    conn = db()
    s = get_settings(conn)
    d = local_now(s).date().isoformat()
    rows = conn.execute("SELECT a.*, st.name, st.roll FROM attendance a JOIN students st ON st.id=a.student_id "
                        "WHERE a.date=? ORDER BY a.updated_at DESC", (d,)).fetchall()
    return jsonify(rows=[{"student_id": r["student_id"], "name": r["name"], "roll": r["roll"], "entry": r["entry_time"],
                          "exit": r["exit_time"], "duration": duration(r["entry_time"], r["exit_time"])} for r in rows],
                   date=d, trained=model is not None)


@app.post("/api/attendance/manual")
@need("edit_attendance")
def attendance_manual():
    body = request.get_json(silent=True) or {}
    conn = db()
    d, sid = valid_date(body.get("date")), body.get("student_id")
    if not d or not conn.execute("SELECT 1 FROM students WHERE id=?", (sid,)).fetchone():
        return jsonify(error="Pick a student and a date."), 400

    def tm(v):
        return v if isinstance(v, str) and re.fullmatch(r"\d\d:\d\d(:\d\d)?", v) and True else None
    entry, exit_ = tm(body.get("entry")), tm(body.get("exit"))
    entry = (entry + ":00") if entry and len(entry) == 5 else entry
    exit_ = (exit_ + ":00") if exit_ and len(exit_) == 5 else exit_
    if not entry:
        return jsonify(error="Enter the entry time."), 400
    conn.execute("INSERT INTO attendance(student_id,date,entry_time,exit_time,source,updated_at) VALUES(?,?,?,?,?,?) "
                 "ON CONFLICT(student_id,date) DO UPDATE SET entry_time=excluded.entry_time, exit_time=excluded.exit_time, "
                 "source='manual', updated_at=excluded.updated_at", (sid, d, entry, exit_, "manual", time.time()))
    conn.commit()
    return jsonify(ok=True)


@app.delete("/api/attendance/<int:sid>/<d>")
@need("edit_attendance")
def attendance_delete(sid, d):
    db().execute("DELETE FROM attendance WHERE student_id=? AND date=?", (sid, d))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/my")
@need("view_own")
def my_attendance():
    user = current_user()
    if not user["student_id"]:
        return jsonify(error="Your login is not linked to a student."), 400
    conn = db()
    st = conn.execute("SELECT * FROM students WHERE id=?", (user["student_id"],)).fetchone()
    if not st:
        return jsonify(error="Student record not found."), 404
    out = summary(conn, st, get_settings(conn))
    out["student"] = student_json(st)
    return jsonify(out)


@app.get("/api/students/<int:sid>/summary")
@need("view_reports")
def student_summary(sid):
    conn = db()
    st = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not st:
        return jsonify(error="Student not found."), 404
    out = summary(conn, st, get_settings(conn))
    out["student"] = student_json(st)
    return jsonify(out)


@app.get("/api/dashboard")
@need("view_reports", "take_attendance", "manage_leaves", "manage_students", any_of=True)
def dashboard():
    conn = db()
    s = get_settings(conn)
    today = local_now(s).date()
    key = today.isoformat()
    holidays, working = holiday_set(conn), set(s["working_days"])
    students = conn.execute("SELECT * FROM students WHERE active=1").fetchall()
    present_ids = {r["student_id"] for r in conn.execute("SELECT student_id FROM attendance WHERE date=? AND entry_time IS NOT NULL", (key,))}
    is_work = today.weekday() in working and key not in holidays
    on_leave = 0
    low = []
    percents = []
    for st in students:
        if is_work and key in approved_leave_days(conn, st["id"]):
            on_leave += 1
        sm = summary(conn, st, s, holidays)
        if sm["percent"] is not None:
            percents.append(sm["percent"])
            if sm["percent"] < 75:
                low.append({"id": st["id"], "name": st["name"], "roll": st["roll"], "percent": sm["percent"]})
    total = len(students)
    present = len([st for st in students if st["id"] in present_ids])
    away = max(total - present - on_leave, 0) if is_work else 0
    counted = total - on_leave
    recent = conn.execute("SELECT a.*, st.name FROM attendance a JOIN students st ON st.id=a.student_id "
                          "ORDER BY a.updated_at DESC LIMIT 8").fetchall()
    pending_leaves = conn.execute("SELECT COUNT(*) FROM leaves WHERE status='pending'").fetchone()[0]
    return jsonify(total_students=total, present_today=present, absent_today=away, on_leave_today=on_leave,
                   attendance_rate=round(present / counted * 100, 1) if is_work and counted > 0 else 0,
                   average_percent=round(sum(percents) / len(percents), 1) if percents else None,
                   is_working_day=is_work, low=sorted(low, key=lambda x: x["percent"])[:6],
                   recent=[{"name": r["name"], "date": r["date"], "entry": r["entry_time"], "exit": r["exit_time"]} for r in recent],
                   pending_leaves=pending_leaves, trained=model is not None, date=key)


# ----------------------------------------------------------------------------
# Leave
# ----------------------------------------------------------------------------
def leave_json(r):
    return {"id": r["id"], "student_id": r["student_id"], "name": r["name"], "roll": r["roll"], "from": r["from_date"],
            "to": r["to_date"], "reason": r["reason"], "status": r["status"], "note": r["note"], "decided_by": r["decided_by"]}


@app.get("/api/leaves")
@need("manage_leaves", "apply_leave", any_of=True)
def leaves_list():
    user, conn = current_user(), db()
    q = "SELECT l.*, st.name, st.roll FROM leaves l JOIN students st ON st.id=l.student_id"
    args = ()
    if "manage_leaves" not in perms_of(user):
        q, args = q + " WHERE l.student_id=?", (user["student_id"],)
    rows = conn.execute(q + " ORDER BY l.applied_at DESC LIMIT 500", args).fetchall()
    return jsonify([leave_json(r) for r in rows])


@app.post("/api/leaves")
@need("manage_leaves", "apply_leave", any_of=True)
def leaves_apply():
    body = request.get_json(silent=True) or {}
    user, conn = current_user(), db()
    staff = "manage_leaves" in perms_of(user)
    sid = body.get("student_id") if staff and body.get("student_id") else user["student_id"]
    if not sid or not conn.execute("SELECT 1 FROM students WHERE id=?", (sid,)).fetchone():
        return jsonify(error="Pick a student."), 400
    a, b = valid_date(body.get("from")), valid_date(body.get("to"))
    if not a or not b or a > b:
        return jsonify(error="Choose a valid From and To date."), 400
    if (date.fromisoformat(b) - date.fromisoformat(a)).days > 60:
        return jsonify(error="One request can cover at most 60 days."), 400
    reason = (body.get("reason") or "").strip()[:200]
    overlap = conn.execute("SELECT 1 FROM leaves WHERE student_id=? AND status<>'rejected' AND from_date<=? AND to_date>=?",
                           (sid, b, a)).fetchone()
    if overlap:
        return jsonify(error="This overlaps another leave request."), 400
    status = "approved" if staff and body.get("approve") else "pending"
    conn.execute("INSERT INTO leaves(student_id,from_date,to_date,reason,status,applied_at,decided_by,decided_at) VALUES(?,?,?,?,?,?,?,?)",
                 (sid, a, b, reason, status, time.time(), user["user_id"] if status == "approved" else None,
                  time.time() if status == "approved" else None))
    conn.commit()
    return jsonify(ok=True, status=status)


@app.post("/api/leaves/<int:lid>/decision")
@need("manage_leaves")
def leaves_decide(lid):
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in ("approved", "rejected", "pending"):
        return jsonify(error="Choose approve or reject."), 400
    conn = db()
    if not conn.execute("SELECT 1 FROM leaves WHERE id=?", (lid,)).fetchone():
        return jsonify(error="Request not found."), 404
    conn.execute("UPDATE leaves SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
                 (status, current_user()["user_id"], time.time(), (body.get("note") or "")[:200], lid))
    conn.commit()
    return jsonify(ok=True)


@app.delete("/api/leaves/<int:lid>")
@need("manage_leaves", "apply_leave", any_of=True)
def leaves_delete(lid):
    user, conn = current_user(), db()
    row = conn.execute("SELECT * FROM leaves WHERE id=?", (lid,)).fetchone()
    if not row:
        return jsonify(error="Request not found."), 404
    if "manage_leaves" not in perms_of(user) and (row["student_id"] != user["student_id"] or row["status"] != "pending"):
        return jsonify(error="You can only cancel your own pending requests."), 403
    conn.execute("DELETE FROM leaves WHERE id=?", (lid,))
    conn.commit()
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Backup (the free hosts reset their disk, so keep a copy)
# ----------------------------------------------------------------------------
@app.get("/api/backup")
@need("backup")
def backup():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db", dir=DATA_DIR)
    tmp.close()
    dest = sqlite3.connect(tmp.name)
    connect().backup(dest)
    dest.close()
    with open(tmp.name, "rb") as f:
        data = f.read()
    os.remove(tmp.name)
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename=attendance-backup-{date.today().isoformat()}.db"})


@app.post("/api/restore")
@need("backup")
def restore():
    upload = request.files.get("file")
    if not upload:
        return jsonify(error="Choose a backup file."), 400
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db", dir=DATA_DIR)
    upload.save(tmp.name)
    tmp.close()
    try:
        src = sqlite3.connect(tmp.name)
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"students", "users", "attendance", "photos", "leaves"} <= tables:
            raise ValueError("not a backup of this app")
        if src.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] == 0:
            raise ValueError("the backup has no admin account")
        dest = connect()
        src.backup(dest)
        dest.close()
        src.close()
    except Exception as e:
        os.remove(tmp.name)
        return jsonify(error=f"That file cannot be restored: {e}"), 400
    os.remove(tmp.name)
    init_db()
    train_model()
    session.clear()
    return jsonify(ok=True, msg="Backup restored. Please log in again.")


# ----------------------------------------------------------------------------
# Optional cloud copy in Upstash Redis.
# Free hosts erase their disk when they restart. With UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN and
# BACKUP_KEY set, an encrypted copy of everything is kept in Upstash and an empty database is refilled from it
# at start-up. Changes are saved about a minute after they happen. Nothing is stored there in readable form.
# ----------------------------------------------------------------------------
CLOUD_URL = (os.environ.get("UPSTASH_REDIS_REST_URL") or "").strip().rstrip("/")
CLOUD_TOKEN = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()
CLOUD_SECRET = os.environ.get("BACKUP_KEY") or os.environ.get("SECRET_KEY") or ""
CLOUD_NS = re.sub(r"[^a-zA-Z0-9_-]", "", os.environ.get("CLOUD_NAMESPACE", "default")) or "default"
CHUNK = 600_000   # Upstash free plan: one request may be at most 1 MB
CLOUD_TABLES = ["settings", "students", "users", "attendance", "leaves", "holidays"]
cloud = {"state": "off", "last_sync": None, "error": "", "dirty_first": None, "dirty_last": None, "sigs": {}}
cloud_lock = threading.RLock()
try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:   # pragma: no cover
    Fernet = InvalidToken = None
cloud_fernet = None
if CLOUD_URL and CLOUD_TOKEN:
    if Fernet is None:
        cloud["error"] = "Install the 'cryptography' package to use the cloud copy."
    elif not CLOUD_SECRET:
        cloud["error"] = "Set BACKUP_KEY on the server so the cloud copy can be decrypted after a restart."
    else:
        cloud["state"] = "starting"
        cloud_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("face-attendance:" + CLOUD_SECRET).encode()).digest()))


def ck(name, part=None):
    return f"fa:{CLOUD_NS}:{name}" + (f":{part}" if part is not None else "")


def cloud_call(commands, pipeline=False, timeout=25):
    req = urllib.request.Request(CLOUD_URL + ("/pipeline" if pipeline else ""), data=json.dumps(commands).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + CLOUD_TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Upstash answered {e.code}") from None
    except Exception as e:
        raise RuntimeError(f"cannot reach Upstash ({e.__class__.__name__})") from None


def cloud_put(name, data):
    token = cloud_fernet.encrypt(zlib.compress(data, 6))   # compress first: attendance rows repeat a lot
    chunks = [token[i:i + CHUNK] for i in range(0, len(token), CHUNK)] or [b""]
    old = cloud_call(["GET", ck(name, "meta")]).get("result")
    old_n = json.loads(old)["n"] if old else 0
    for i, chunk in enumerate(chunks):
        cloud_call(["SET", ck(name, i), chunk.decode()])
    cloud_call(["SET", ck(name, "meta"), json.dumps({"n": len(chunks), "sha": hashlib.sha256(token).hexdigest()})])
    if old_n > len(chunks):
        cloud_call([["DEL", ck(name, i)] for i in range(len(chunks), old_n)], pipeline=True)


def cloud_get(name):
    raw = cloud_call(["GET", ck(name, "meta")]).get("result")
    if not raw:
        return None
    meta = json.loads(raw)
    res = cloud_call([["GET", ck(name, i)] for i in range(meta["n"])], pipeline=True)
    token = "".join((r.get("result") or "") for r in res).encode()
    if hashlib.sha256(token).hexdigest() != meta["sha"]:
        raise RuntimeError("the cloud copy is incomplete")
    try:
        return zlib.decompress(cloud_fernet.decrypt(token))
    except InvalidToken:
        raise RuntimeError("cannot decrypt the cloud copy: BACKUP_KEY is different from the one used to save it") from None


def cloud_del(name):
    raw = cloud_call(["GET", ck(name, "meta")]).get("result")
    if raw:
        n = json.loads(raw)["n"]
        cloud_call([["DEL", ck(name, "meta")]] + [["DEL", ck(name, i)] for i in range(n)], pipeline=True)


def photo_sigs(conn):
    """A fingerprint of each student's photos, so only changed photos are uploaded again."""
    sigs = {}
    for (sid,) in conn.execute("SELECT DISTINCT student_id FROM photos").fetchall():
        h = hashlib.sha256()
        n = 0
        for (jpeg,) in conn.execute("SELECT jpeg FROM photos WHERE student_id=? ORDER BY id", (sid,)):
            h.update(jpeg)
            n += 1
        sigs[str(sid)] = f"{n}:{h.hexdigest()[:16]}"
    return sigs


def touch():
    """Remember that something changed, so it is saved to the cloud soon."""
    if cloud["state"] == "ok":
        now = time.time()
        cloud["dirty_first"] = cloud["dirty_first"] or now
        cloud["dirty_last"] = now


def cloud_sync(force=False):
    with cloud_lock:
        if cloud["state"] != "ok":
            raise RuntimeError("the cloud copy is not active" + (": " + cloud["error"] if cloud["error"] else ""))
        cloud["dirty_first"] = cloud["dirty_last"] = None
        conn = connect()
        try:
            if not conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                return
            sigs = photo_sigs(conn)
            for sid, sig in sigs.items():
                if force or cloud["sigs"].get(sid) != sig:
                    photos = [base64.b64encode(r[0]).decode() for r in conn.execute("SELECT jpeg FROM photos WHERE student_id=? ORDER BY id", (sid,))]
                    cloud_put(f"photos:{sid}", json.dumps(photos).encode())
            for sid in set(cloud["sigs"]) - set(sigs):
                cloud_del(f"photos:{sid}")
            core = {"v": 1, "saved": time.time(), "photo_sigs": sigs}
            for table in CLOUD_TABLES:
                core[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            cloud_put("core", json.dumps(core).encode())   # written last: this is the commit point
            cloud["sigs"], cloud["last_sync"], cloud["error"] = sigs, time.time(), ""
        except Exception as e:
            cloud["error"] = str(e)
            cloud["dirty_first"] = cloud["dirty_last"] = time.time()   # try again in a minute
            raise
        finally:
            conn.close()


def cloud_restore():
    """Fill an empty database from the cloud copy. Returns True if there was a copy."""
    raw = cloud_get("core")
    if raw is None:
        return False
    core = json.loads(raw)
    conn = connect()
    try:
        conn.execute("BEGIN")
        for table in CLOUD_TABLES:
            cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
            for row in core.get(table, []):
                keys = [k for k in row if k in cols]
                conn.execute(f"INSERT OR REPLACE INTO {table}({','.join(keys)}) VALUES({','.join('?' * len(keys))})", [row[k] for k in keys])
        for st in core.get("students", []):
            blob = cloud_get(f"photos:{st['id']}")
            for b in (json.loads(blob) if blob else []):
                conn.execute("INSERT INTO photos(student_id, jpeg) VALUES(?,?)", (st["id"], base64.b64decode(b)))
        conn.commit()
        cloud["sigs"] = photo_sigs(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return True


def cloud_startup():
    """Run once at start, and again every 30 seconds while the cloud could not be reached."""
    if cloud["state"] not in ("starting", "failed"):
        return
    cloud["state"] = "starting"
    try:
        conn = connect()
        empty = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        conn.close()
        if empty:
            cloud_restore()
        else:
            raw = cloud_get("core")
            cloud["sigs"] = json.loads(raw).get("photo_sigs", {}) if raw else {}
        cloud["state"], cloud["error"] = "ok", ""
        train_model()
    except Exception as e:
        cloud["state"], cloud["error"] = "failed", str(e)


def cloud_tick():
    """One check: save pending changes once things have been quiet for a minute (or five minutes at most)."""
    if cloud["state"] == "failed":
        cloud_startup()
    elif cloud["state"] == "ok" and cloud["dirty_first"] is not None:
        now = time.time()
        if now - cloud["dirty_last"] >= 60 or now - cloud["dirty_first"] >= 300:
            cloud_sync()


def cloud_worker():
    while True:
        time.sleep(15)
        try:
            cloud_tick()
        except Exception:
            pass   # the error is kept in cloud["error"] and shown in Settings


@app.after_request
def note_changes(resp):
    skip = ("/api/recognize", "/api/selfcheck", "/api/enroll", "/api/login", "/api/logout", "/api/cloud")
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/") and resp.status_code < 400 and not request.path.startswith(skip):
        touch()
    return resp


@app.get("/api/cloud")
@need("backup")
def cloud_status():
    return jsonify(configured=bool(CLOUD_URL and CLOUD_TOKEN), state=cloud["state"], last_sync=cloud["last_sync"], error=cloud["error"],
                   pending=cloud["dirty_first"] is not None, namespace=CLOUD_NS)


@app.post("/api/cloud/sync")
@need("backup")
def cloud_sync_now():
    try:
        cloud_sync(force=bool((request.get_json(silent=True) or {}).get("force")))
    except Exception as e:
        return jsonify(error=f"Cloud save failed: {e}"), 502
    return jsonify(ok=True, last_sync=cloud["last_sync"])


# ----------------------------------------------------------------------------
# Pages, app install files
# ----------------------------------------------------------------------------
def make_icon(size):
    img = np.full((size, size, 3), (214, 87, 36), np.uint8)
    white, c = (255, 255, 255), size // 2
    t, m, n = max(2, size // 24), int(size * 0.2), int(size * 0.16)
    for x, y, dx, dy in [(m, m, 1, 1), (size - m, m, -1, 1), (m, size - m, 1, -1), (size - m, size - m, -1, -1)]:
        cv2.line(img, (x, y), (x + dx * n, y), white, t)
        cv2.line(img, (x, y), (x, y + dy * n), white, t)
    cv2.circle(img, (c, int(size * 0.42)), int(size * 0.12), white, -1)
    cv2.ellipse(img, (c, int(size * 0.74)), (int(size * 0.2), int(size * 0.16)), 0, 180, 360, white, -1)
    return cv2.imencode(".png", img)[1].tobytes()


ICONS = {192: make_icon(192), 512: make_icon(512)}
SW = """
const OFFLINE = '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font-family:system-ui,sans-serif;max-width:480px;margin:40px auto;padding:0 20px"><h2>The server is offline</h2><p>It may be restarting or waking up. Wait a minute and reload.</p><p><button onclick="location.reload()" style="padding:10px 18px;font-size:16px">Reload</button></p>';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).then(r => r.status >= 500 ? new Response(OFFLINE, {headers: {'Content-Type': 'text/html'}}) : r)
    .catch(() => new Response(OFFLINE, {headers: {'Content-Type': 'text/html'}})));
});
"""


@app.get("/")
def index():
    resp = send_file(os.path.join(BASE, "templates", "index.html"))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    data = {"name": "Face Attendance", "short_name": "Attendance", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#f4f5f8", "theme_color": "#4f46e5",
            "icons": [{"src": f"/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"} for n in ICONS]}
    return Response(json.dumps(data), mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    r = Response(SW, mimetype="application/javascript")
    r.headers["Service-Worker-Allowed"] = "/"
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/icon-<int:size>.png")
def icon(size):
    if size not in ICONS:
        abort(404)
    r = Response(ICONS[size], mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


@app.get("/health")
def health():
    return jsonify(ok=True, trained=model is not None)


@app.errorhandler(413)
def too_big(_e):
    return jsonify(error="That upload is too large."), 413


# ----------------------------------------------------------------------------
init_db()
cloud_startup()   # an empty database is refilled from the cloud copy before anybody can use the app
if os.environ.get("ADMIN_PASSWORD") and cloud["state"] not in ("starting", "failed"):
    _c = connect()
    if not _c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        create_user(_c, "admin", "Administrator", "admin", os.environ["ADMIN_PASSWORD"])
        _c.commit()
    _c.close()
train_model()
if cloud["state"] != "off":
    threading.Thread(target=cloud_worker, daemon=True).start()


def run(port=None):
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(port or os.environ.get("PORT", 5000)),
            threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    print("\nOpen http://127.0.0.1:%s in your browser\n" % os.environ.get("PORT", 5000))
    run()
