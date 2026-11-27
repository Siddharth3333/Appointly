import os, re, secrets
from datetime import datetime, timedelta, timezone, date, time
from zoneinfo import ZoneInfo
from functools import wraps
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import UniqueConstraint, Index
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()
app = Flask(__name__, static_folder="static")
db_url = os.getenv("DATABASE_URL", "sqlite:///appointly.db").replace("postgres://", "postgresql://", 1)
app.config.update(SECRET_KEY=os.getenv("SECRET_KEY", "dev-only"), SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 280},
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("APP_URL", "").startswith("https"))
db = SQLAlchemy(app); Migrate(app, db)
utcnow = lambda: datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC everywhere internally

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    business_name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    timezone = db.Column(db.String(60), default="UTC", nullable=False)
    buffer_min = db.Column(db.Integer, default=0, nullable=False)
    min_notice_h = db.Column(db.Integer, default=2, nullable=False)
    max_days = db.Column(db.Integer, default=60, nullable=False)
    reminder_hours = db.Column(db.String(60), default="24,2", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(40)); notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "email"),)

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False); description = db.Column(db.Text)
    duration_min = db.Column(db.Integer, nullable=False); price = db.Column(db.Numeric(10, 2), default=0)
    color = db.Column(db.String(9), default="#0f766e"); active = db.Column(db.Boolean, default=True)

class Availability(db.Model):  # one window per weekday (0=Mon); absent = closed
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    start = db.Column(db.String(5), nullable=False); end = db.Column(db.String(5), nullable=False)
    break_start = db.Column(db.String(5)); break_end = db.Column(db.String(5))
    __table_args__ = (UniqueConstraint("user_id", "weekday"),)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id", ondelete="CASCADE"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False)
    start_utc = db.Column(db.DateTime, nullable=False); end_utc = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default="confirmed", nullable=False)
    notes = db.Column(db.Text); token = db.Column(db.String(64), default=lambda: secrets.token_urlsafe(24), unique=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    client = db.relationship("Client"); service = db.relationship("Service")
    __table_args__ = (Index("ix_appt_user_start", "user_id", "start_utc"),)

class RateLimit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), unique=True, nullable=False)
    window_start = db.Column(db.DateTime, nullable=False); count = db.Column(db.Integer, default=0, nullable=False)

class Blocked(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    start_utc = db.Column(db.DateTime, nullable=False); end_utc = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.String(120))

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(30), nullable=False); recipient = db.Column(db.String(255), nullable=False)
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(12), default="pending", nullable=False)  # pending|sent|failed
    attempts = db.Column(db.Integer, default=0); error = db.Column(db.Text)
    attempted_at = db.Column(db.DateTime); sent_at = db.Column(db.DateTime)
    __table_args__ = (UniqueConstraint("appointment_id", "kind"),)  # one per appointment per interval

STATUSES = {"pending", "confirmed", "completed", "cancelled", "no_show"}
def client_ip(): return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
def rate_limited(key, limit, window=900):
    """DB-backed fixed-window limiter: survives restarts and is shared across workers."""
    try:
        now = utcnow(); r = RateLimit.query.filter_by(key=key[:200]).with_for_update().first()
        if not r: r = RateLimit(key=key[:200], window_start=now, count=0); db.session.add(r)
        elif (now - r.window_start).total_seconds() > window: r.window_start, r.count = now, 0
        r.count += 1; db.session.commit(); return r.count > limit
    except Exception: db.session.rollback(); return False
def tz(u): return ZoneInfo(u.timezone)
def err(m, c=400): return jsonify(error=m), c
def login_required(f):
    @wraps(f)
    def w(*a, **k):
        u = db.session.get(User, session.get("uid"))
        if not u: return err("Not signed in", 401)
        if request.method not in ("GET", "HEAD"):
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")): return err("CSRF check failed", 403)
            if not request.is_json and request.content_length: return err("JSON required", 415)
        return f(u, *a, **k)
    return w

def row(o, *f): return {k: (v.isoformat() + "Z" if isinstance(v := getattr(o, k), datetime) else float(v) if k == "price" else v) for k in f}
def appt_json(a):
    return {**row(a, "id", "status", "notes", "start_utc", "end_utc", "token"), "client": row(a.client, "id", "name", "email", "phone"),
            "service": row(a.service, "id", "name", "color", "price", "duration_min")}

# ---------- email ----------
def send_email(to, subject, html):
    key = os.getenv("RESEND_API_KEY")
    if not key: raise RuntimeError("RESEND_API_KEY not set")
    r = requests.post("https://api.resend.com/emails", timeout=10, headers={"Authorization": f"Bearer {key}"},
        json={"from": os.getenv("MAIL_FROM", "Appointly <onboarding@resend.dev>"), "to": [to], "subject": subject, "html": html})
    if r.status_code >= 300: raise RuntimeError(f"Resend {r.status_code}: {r.text[:200]}")

def render_notification(n):
    a = db.session.get(Appointment, n.appointment_id); u = db.session.get(User, a.user_id)
    when = a.start_utc.replace(tzinfo=timezone.utc).astimezone(tz(u)).strftime("%A %d %b %Y, %H:%M ") + u.timezone
    link = f"{os.getenv('APP_URL','')}/manage/{a.token}"
    subj = {"confirmation": "Booking confirmed", "cancellation": "Appointment cancelled", "rescheduled": "Appointment rescheduled",
            "followup": "Thanks for visiting"}.get(n.kind, "Appointment reminder")
    body = f"<p>Hi {a.client.name},</p><p>{subj}: <b>{a.service.name}</b> with {u.business_name} on <b>{when}</b>.</p>"
    if n.kind != "cancellation": body += f"<p><a href='{link}'>Reschedule or cancel</a></p>"
    return f"{subj} · {u.business_name}", body

def queue(a, kinds_now=("confirmation",)):
    u = db.session.get(User, a.user_id)
    for k in kinds_now: db.session.add(Notification(appointment_id=a.id, kind=k, recipient=a.client.email, due_at=utcnow()))
    if a.status in ("pending", "confirmed"):
        for h in [x for x in u.reminder_hours.split(",") if x.strip().isdigit()]:
            due = a.start_utc - timedelta(hours=int(h))
            if due > utcnow(): db.session.add(Notification(appointment_id=a.id, kind=f"reminder_{h}h", recipient=a.client.email, due_at=due))

def drop_unsent_reminders(a):
    Notification.query.filter(Notification.appointment_id == a.id, Notification.status != "sent",
                              Notification.kind.like("reminder_%")).delete(synchronize_session=False)

@app.post("/api/cron/reminders")
def cron_reminders():
    secret = os.getenv("CRON_SECRET")
    if not secret or not secrets.compare_digest(request.headers.get("Authorization", ""), f"Bearer {secret}"): return err("Forbidden", 403)
    now = utcnow(); sent = failed = 0
    rows = (Notification.query.filter(Notification.status.in_(["pending", "failed"]), Notification.attempts < 3, Notification.due_at <= now)
            .order_by(Notification.due_at).limit(50).with_for_update(skip_locked=True).all())
    for n in rows:
        a = db.session.get(Appointment, n.appointment_id)
        if n.kind.startswith("reminder_") and (a.status not in ("pending", "confirmed") or a.start_utc < now):
            n.status, n.error = "failed", "skipped: appointment no longer upcoming"; n.attempts = 99; continue
        n.attempts += 1; n.attempted_at = now
        try:
            send_email(n.recipient, *render_notification(n)); n.status, n.sent_at, n.error = "sent", utcnow(), None; sent += 1
        except Exception as e:
            n.status, n.error = "failed", str(e)[:500]; failed += 1
        db.session.commit()  # commit per row: a crash can't resend earlier rows
    db.session.commit()
    return jsonify(processed=len(rows), sent=sent, failed=failed)

# ---------- scheduling ----------
def conflicts(uid, s, e, buffer_min, exclude=None):
    if Blocked.query.filter(Blocked.user_id == uid, Blocked.start_utc < e, Blocked.end_utc > s).first(): return True
    q = Appointment.query.filter(Appointment.user_id == uid, Appointment.status.in_(["pending", "confirmed"]),
        Appointment.start_utc < e + timedelta(minutes=buffer_min), Appointment.end_utc > s - timedelta(minutes=buffer_min))
    if exclude: q = q.filter(Appointment.id != exclude)
    return q.first() is not None

def free_slots(u, svc, day):
    w = Availability.query.filter_by(user_id=u.id, weekday=day.weekday()).first()
    if not w: return []
    z = tz(u); sh, sm = map(int, w.start.split(":")); eh, em = map(int, w.end.split(":"))
    cur = datetime.combine(day, time(sh, sm), z); end = datetime.combine(day, time(eh, em), z); out = []
    earliest = utcnow() + timedelta(hours=u.min_notice_h); latest = utcnow() + timedelta(days=u.max_days)
    bs = be = None
    if w.break_start and w.break_end:
        bs = datetime.combine(day, time(*map(int, w.break_start.split(":"))), z); be = datetime.combine(day, time(*map(int, w.break_end.split(":"))), z)
    while cur + timedelta(minutes=svc.duration_min) <= end:
        if bs and cur < be and cur + timedelta(minutes=svc.duration_min) > bs: cur += timedelta(minutes=15); continue
        s = cur.astimezone(timezone.utc).replace(tzinfo=None); e = s + timedelta(minutes=svc.duration_min)
        if earliest <= s <= latest and not conflicts(u.id, s, e, u.buffer_min): out.append(s.isoformat() + "Z")
        cur += timedelta(minutes=15)
    return out

def create_appt(u, svc, client, start, status="confirmed", notes=None):
    db.session.execute(db.select(User).where(User.id == u.id).with_for_update())  # serialize bookings per business
    end = start + timedelta(minutes=svc.duration_min)
    if conflicts(u.id, start, end, u.buffer_min): return None
    a = Appointment(user_id=u.id, client_id=client.id, service_id=svc.id, start_utc=start, end_utc=end, status=status, notes=notes)
    db.session.add(a); db.session.flush(); queue(a); db.session.commit(); return a

def parse_dt(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
def upsert_client(uid, name, email, phone=None):
    email = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not name.strip(): return None
    c = Client.query.filter_by(user_id=uid, email=email).first()
    if not c: c = Client(user_id=uid, name=name.strip()[:120], email=email, phone=phone); db.session.add(c); db.session.flush()
    return c

# ---------- auth ----------
@app.post("/api/auth/register")
def register():
    if rate_limited(f"register:{client_ip()}", 10, 3600): return err("Too many sign-ups, try later", 429)
    d = request.get_json(silent=True) or {}
    email, pw, name, biz = (d.get(k, "").strip() for k in ("email", "password", "name", "business_name"))
    if not (email and name and biz) or len(pw) < 8: return err("Name, business, email and a password of 8+ characters are required")
    if User.query.filter_by(email=email.lower()).first(): return err("Email already registered", 409)
    slug = re.sub(r"[^a-z0-9]+", "-", biz.lower()).strip("-") or "business"
    if User.query.filter_by(slug=slug).first(): slug += "-" + secrets.token_hex(2)
    u = User(email=email.lower(), name=name, password_hash=generate_password_hash(pw), business_name=biz, slug=slug,
             timezone=d.get("timezone") if d.get("timezone") in __import__("zoneinfo").available_timezones() else "UTC")
    db.session.add(u); db.session.flush()
    for wd in range(5): db.session.add(Availability(user_id=u.id, weekday=wd, start="09:00", end="17:00"))
    db.session.commit(); session["uid"] = u.id; session["csrf"] = secrets.token_urlsafe(24)
    return jsonify(id=u.id, slug=slug)

@app.post("/api/auth/login")
def login():
    d = request.get_json(silent=True) or {}
    if rate_limited(f"login:{client_ip()}:{(d.get('email') or '').lower()}", 10): return err("Too many attempts, try again in 15 minutes", 429)
    u = User.query.filter_by(email=(d.get("email") or "").lower()).first()
    if not u or not check_password_hash(u.password_hash, d.get("password", "")): return err("Invalid email or password", 401)
    session.clear(); session["uid"] = u.id; session["csrf"] = secrets.token_urlsafe(24); return jsonify(id=u.id)

@app.post("/api/auth/logout")
def logout(): session.clear(); return jsonify(ok=True)
@app.get("/api/me")
@login_required
def me(u):
    session.setdefault("csrf", secrets.token_urlsafe(24)); return jsonify(csrf=session["csrf"], **row(u, "id", "name", "email", "business_name", "slug", "timezone", "buffer_min", "min_notice_h", "max_days", "reminder_hours"))
@app.patch("/api/me")
@login_required
def me_update(u):
    d = request.get_json() or {}
    for k in ("business_name", "buffer_min", "min_notice_h", "max_days", "reminder_hours"):
        if k in d: setattr(u, k, d[k])
    if d.get("timezone") in __import__("zoneinfo").available_timezones(): u.timezone = d["timezone"]
    db.session.commit(); return jsonify(ok=True)

# ---------- services / clients / availability ----------
@app.route("/api/services", methods=["GET", "POST"])
@login_required
def services(u):
    if request.method == "POST":
        d = request.get_json() or {}
        try: dur = int(d["duration_min"]); assert 5 <= dur <= 600; assert d["name"].strip()
        except Exception: return err("Name and duration (5-600 min) required")
        s = Service(user_id=u.id, name=d["name"].strip(), description=d.get("description"), duration_min=dur, price=d.get("price") or 0, color=d.get("color", "#0f766e"))
        db.session.add(s); db.session.commit()
    return jsonify([row(s, "id", "name", "description", "duration_min", "price", "color", "active") for s in Service.query.filter_by(user_id=u.id)])

@app.route("/api/services/<int:i>", methods=["PATCH", "DELETE"])
@login_required
def service_one(u, i):
    s = Service.query.filter_by(id=i, user_id=u.id).first_or_404()
    if request.method == "DELETE":
        if Appointment.query.filter_by(service_id=i).first(): s.active = False  # keep history
        else: db.session.delete(s)
    else:
        for k in ("name", "description", "duration_min", "price", "color", "active"):
            if k in (request.get_json() or {}): setattr(s, k, request.json[k])
    db.session.commit(); return jsonify(ok=True)

@app.route("/api/clients", methods=["GET", "POST"])
@login_required
def clients(u):
    if request.method == "POST":
        d = request.get_json() or {}; c = upsert_client(u.id, d.get("name", ""), d.get("email", ""), d.get("phone"))
        if not c: return err("Valid name and email required")
        c.notes = d.get("notes", c.notes); db.session.commit()
    q = Client.query.filter_by(user_id=u.id)
    if request.args.get("q"): q = q.filter(db.or_(Client.name.ilike(f"%{request.args['q']}%"), Client.email.ilike(f"%{request.args['q']}%")))
    return jsonify([{**row(c, "id", "name", "email", "phone", "notes"), "appointments": Appointment.query.filter_by(client_id=c.id).count()} for c in q.order_by(Client.name)])

@app.delete("/api/clients/<int:i>")
@login_required
def client_del(u, i): db.session.delete(Client.query.filter_by(id=i, user_id=u.id).first_or_404()); db.session.commit(); return jsonify(ok=True)

@app.route("/api/availability", methods=["GET", "PUT"])
@login_required
def availability(u):
    if request.method == "PUT":
        Availability.query.filter_by(user_id=u.id).delete()
        for r in request.get_json() or []:
            if re.match(r"^\d\d:\d\d$", r["start"]) and re.match(r"^\d\d:\d\d$", r["end"]) and r["start"] < r["end"] and 0 <= int(r["weekday"]) <= 6:
                bs, be = r.get("break_start") or None, r.get("break_end") or None
                if not (bs and be and r["start"] <= bs < be <= r["end"]): bs = be = None
                db.session.add(Availability(user_id=u.id, weekday=int(r["weekday"]), start=r["start"], end=r["end"], break_start=bs, break_end=be))
        db.session.commit()
    return jsonify([row(a, "weekday", "start", "end", "break_start", "break_end") for a in Availability.query.filter_by(user_id=u.id)])

# ---------- appointments ----------
@app.route("/api/appointments", methods=["GET", "POST"])
@login_required
def appointments(u):
    if request.method == "POST":
        d = request.get_json() or {}
        svc = Service.query.filter_by(id=d.get("service_id"), user_id=u.id).first()
        c = upsert_client(u.id, d.get("name", ""), d.get("email", ""), d.get("phone"))
        try: start = parse_dt(d["start"])
        except Exception: return err("Valid start time required")
        if not svc or not c: return err("Service, client name and valid email required")
        a = create_appt(u, svc, c, start, notes=d.get("notes"))
        if not a: return err("That time conflicts with another appointment", 409)
        return jsonify(appt_json(a)), 201
    q = Appointment.query.filter_by(user_id=u.id)
    if request.args.get("status"): q = q.filter_by(status=request.args["status"])
    if request.args.get("q"):
        q = q.join(Client).filter(db.or_(Client.name.ilike(f"%{request.args['q']}%"), Client.email.ilike(f"%{request.args['q']}%"), Client.phone.ilike(f"%{request.args['q']}%")))
    return jsonify([appt_json(a) for a in q.order_by(Appointment.start_utc.desc()).limit(200)])

@app.route("/api/appointments/<int:i>", methods=["PATCH", "DELETE"])
@login_required
def appointment_one(u, i):
    a = Appointment.query.filter_by(id=i, user_id=u.id).first_or_404()
    if request.method == "DELETE": db.session.delete(a); db.session.commit(); return jsonify(ok=True)
    d = request.get_json() or {}
    if "service_id" in d and d["service_id"] != a.service_id:
        sv = Service.query.filter_by(id=d["service_id"], user_id=u.id).first()
        if not sv: return err("Unknown service")
        if conflicts(u.id, a.start_utc, a.start_utc + timedelta(minutes=sv.duration_min), u.buffer_min, exclude=a.id): return err("New duration conflicts with another appointment", 409)
        a.service = sv; a.end_utc = a.start_utc + timedelta(minutes=sv.duration_min)
    if "start" in d:  # reschedule
        s = parse_dt(d["start"]); e = s + timedelta(minutes=a.service.duration_min)
        if conflicts(u.id, s, e, u.buffer_min, exclude=a.id): return err("That time conflicts with another appointment", 409)
        a.start_utc, a.end_utc = s, e; drop_unsent_reminders(a); db.session.flush(); queue(a, ()); 
        db.session.add(Notification(appointment_id=a.id, kind=f"rescheduled", recipient=a.client.email, due_at=utcnow())) if not Notification.query.filter_by(appointment_id=a.id, kind="rescheduled").first() else None
    if "status" in d:
        if d["status"] not in STATUSES: return err("Invalid status")
        a.status = d["status"]
        if a.status == "cancelled":
            drop_unsent_reminders(a)
            if not Notification.query.filter_by(appointment_id=a.id, kind="cancellation").first():
                db.session.add(Notification(appointment_id=a.id, kind="cancellation", recipient=a.client.email, due_at=utcnow()))
        if a.status == "completed" and not Notification.query.filter_by(appointment_id=a.id, kind="followup").first():
            db.session.add(Notification(appointment_id=a.id, kind="followup", recipient=a.client.email, due_at=utcnow() + timedelta(hours=2)))
    if "notes" in d: a.notes = d["notes"]
    db.session.commit(); return jsonify(appt_json(a))

@app.get("/api/stats")
@login_required
def stats(u):
    now = utcnow(); z = tz(u); today = datetime.now(z).date()
    t0 = datetime.combine(today, time(0), z).astimezone(timezone.utc).replace(tzinfo=None)
    A = Appointment.query.filter_by(user_id=u.id); cnt = lambda s: A.filter_by(status=s).count()
    rev = sum(float(a.service.price or 0) for a in A.filter_by(status="completed"))
    return jsonify(total=A.count(), today=A.filter(Appointment.start_utc >= t0, Appointment.start_utc < t0 + timedelta(days=1), Appointment.status.in_(["pending", "confirmed"])).count(),
        upcoming=A.filter(Appointment.start_utc >= now, Appointment.status.in_(["pending", "confirmed"])).count(), completed=cnt("completed"),
        cancelled=cnt("cancelled"), no_show=cnt("no_show"), clients=Client.query.filter_by(user_id=u.id).count(), revenue=rev)

@app.get("/api/notifications")
@login_required
def notifs(u):
    q = Notification.query.join(Appointment).filter(Appointment.user_id == u.id).order_by(Notification.due_at.desc()).limit(100)
    return jsonify([{**row(n, "id", "kind", "recipient", "status", "error", "due_at", "sent_at", "attempts"), "appointment_id": n.appointment_id} for n in q])

# ---------- blocked time, analytics, CSV ----------
@app.route("/api/blocked", methods=["GET", "POST"])
@login_required
def blocked(u):
    if request.method == "POST":
        d = request.get_json() or {}
        try: a, b = parse_dt(d["start"]), parse_dt(d["end"]); assert a < b
        except Exception: return err("Valid start before end required")
        db.session.add(Blocked(user_id=u.id, start_utc=a, end_utc=b, reason=(d.get("reason") or "")[:120])); db.session.commit()
    return jsonify([row(b, "id", "start_utc", "end_utc", "reason") for b in Blocked.query.filter_by(user_id=u.id).order_by(Blocked.start_utc)])

@app.delete("/api/blocked/<int:i>")
@login_required
def blocked_del(u, i): db.session.delete(Blocked.query.filter_by(id=i, user_id=u.id).first_or_404()); db.session.commit(); return jsonify(ok=True)

@app.get("/api/analytics")
@login_required
def analytics(u):
    days = min(request.args.get("days", 30, type=int), 365); since = utcnow() - timedelta(days=days)
    A = Appointment.query.filter(Appointment.user_id == u.id, Appointment.start_utc >= since).all(); n = len(A) or 1
    daily, svc, rev, monthly = {}, {}, 0.0, {}
    for a in A:
        k = a.start_utc.date().isoformat(); daily[k] = daily.get(k, 0) + 1; monthly[k[:7]] = monthly.get(k[:7], 0) + 1
        svc[a.service.name] = svc.get(a.service.name, 0) + 1
        if a.status == "completed": rev += float(a.service.price or 0)
    cl = Client.query.filter(Client.user_id == u.id, Client.created_at >= since).count()
    c = lambda s: sum(a.status == s for a in A)
    return jsonify(total=len(A), completed=c("completed"), cancellation_rate=round(100 * c("cancelled") / n, 1), no_show_rate=round(100 * c("no_show") / n, 1),
        revenue=rev, new_clients=cl, daily=sorted(daily.items()), monthly=sorted(monthly.items()), services=sorted(svc.items(), key=lambda x: -x[1]))

@app.get("/api/export/appointments.csv")
@login_required
def export_csv(u):
    import csv, io
    o = io.StringIO(); w = csv.writer(o); w.writerow(["start_utc", "end_utc", "client", "email", "phone", "service", "status", "notes"])
    for a in Appointment.query.filter_by(user_id=u.id).order_by(Appointment.start_utc):
        w.writerow([c if not str(c).startswith(("=", "+", "-", "@")) else "'" + str(c) for c in
                    [a.start_utc.isoformat(), a.end_utc.isoformat(), a.client.name, a.client.email, a.client.phone or "", a.service.name, a.status, a.notes or ""]])
    return app.response_class(o.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=appointments.csv"})

@app.post("/api/import/clients")
@login_required
def import_clients(u):
    import csv, io
    added = skipped = 0
    for r in csv.DictReader(io.StringIO((request.get_json() or {}).get("csv", ""))):
        r = {k.strip().lower(): (v or "") for k, v in r.items() if k}
        if Client.query.filter_by(user_id=u.id, email=r.get("email", "").strip().lower()).first(): skipped += 1; continue
        c = upsert_client(u.id, r.get("name", ""), r.get("email", ""), r.get("phone"))
        if c: added += 1
        else: skipped += 1
    db.session.commit(); return jsonify(added=added, skipped=skipped)

@app.get("/api/search")
@login_required
def search(u):
    q = (request.args.get("q") or "").strip()
    if len(q) < 2: return jsonify(clients=[], appointments=[])
    like = f"%{q}%"; cm = db.or_(Client.name.ilike(like), Client.email.ilike(like), Client.phone.ilike(like))
    cl = Client.query.filter(Client.user_id == u.id, cm).limit(10).all()
    ap = (Appointment.query.join(Client, Appointment.client_id == Client.id).join(Service, Appointment.service_id == Service.id)
          .filter(Appointment.user_id == u.id, db.or_(cm, Service.name.ilike(like), Appointment.notes.ilike(like))).order_by(Appointment.start_utc.desc()).limit(15).all())
    return jsonify(clients=[row(c, "id", "name", "email", "phone") for c in cl], appointments=[appt_json(a) for a in ap])

@app.route("/api/clients/<int:i>", methods=["GET", "PATCH"])
@login_required
def client_one(u, i):
    c = Client.query.filter_by(id=i, user_id=u.id).first_or_404()
    if request.method == "PATCH":
        d = request.get_json() or {}
        for k in ("name", "phone", "notes"):
            if k in d: setattr(c, k, d[k])
        db.session.commit()
    A = Appointment.query.filter_by(client_id=c.id).order_by(Appointment.start_utc.desc()).all(); now = utcnow()
    up = [a for a in A if a.start_utc >= now and a.status in ("pending", "confirmed")]
    past = [a for a in A if a.start_utc < now]
    return jsonify(**row(c, "id", "name", "email", "phone", "notes", "created_at"), total=len(A), last=past[0].start_utc.isoformat() + "Z" if past else None,
                   upcoming=[appt_json(a) for a in reversed(up)], history=[appt_json(a) for a in past])

@app.get("/api/notifications/summary")
@login_required
def notif_summary(u):
    q = Notification.query.join(Appointment).filter(Appointment.user_id == u.id)
    return jsonify(failed=q.filter(Notification.status == "failed").count(), pending=q.filter(Notification.status == "pending").count())

# ---------- public booking & manage links ----------
@app.get("/api/public/<slug>")
def pub_info(slug):
    u = User.query.filter_by(slug=slug).first_or_404()
    return jsonify(business=u.business_name, timezone=u.timezone, services=[row(s, "id", "name", "description", "duration_min", "price") for s in Service.query.filter_by(user_id=u.id, active=True)])

@app.get("/api/public/<slug>/slots")
def pub_slots(slug):
    u = User.query.filter_by(slug=slug).first_or_404()
    svc = Service.query.filter_by(id=request.args.get("service_id", type=int), user_id=u.id, active=True).first_or_404()
    try: day = date.fromisoformat(request.args["date"])
    except Exception: return err("date=YYYY-MM-DD required")
    return jsonify(free_slots(u, svc, day))

@app.post("/api/public/<slug>/book")
def pub_book(slug):
    if rate_limited(f"book:{client_ip()}", 20, 3600): return err("Too many booking attempts, try later", 429)
    u = User.query.filter_by(slug=slug).first_or_404(); d = request.get_json(silent=True) or {}
    svc = Service.query.filter_by(id=d.get("service_id"), user_id=u.id, active=True).first()
    c = upsert_client(u.id, d.get("name", ""), d.get("email", ""), d.get("phone"))
    try: start = parse_dt(d["start"])
    except Exception: return err("Pick a time slot")
    if not svc or not c: return err("Name and valid email required")
    if d["start"] not in free_slots(u, svc, start.replace(tzinfo=timezone.utc).astimezone(tz(u)).date()): return err("That slot is no longer available", 409)
    a = create_appt(u, svc, c, start)
    if not a: return err("That slot was just taken", 409)
    return jsonify(token=a.token, start=a.start_utc.isoformat() + "Z", service=svc.name), 201

@app.get("/api/manage/<token>")
def manage_get(token):
    a = Appointment.query.filter_by(token=token).first_or_404(); u = db.session.get(User, a.user_id)
    return jsonify(**appt_json(a), business=u.business_name, slug=u.slug, timezone=u.timezone)

@app.post("/api/manage/<token>/cancel")
def manage_cancel(token):
    a = Appointment.query.filter_by(token=token).first_or_404()
    if a.status in ("pending", "confirmed"):
        a.status = "cancelled"; drop_unsent_reminders(a)
        db.session.add(Notification(appointment_id=a.id, kind="cancellation", recipient=a.client.email, due_at=utcnow())); db.session.commit()
    return jsonify(ok=True)

@app.get("/healthz")
def health():
    try: db.session.execute(db.text("select 1")); return jsonify(ok=True)
    except Exception as e: return err(f"db: {e}", 503)

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
UI = DIST if os.path.isfile(os.path.join(DIST, "index.html")) else os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

@app.get("/assets/<path:f>")
def assets(f): return send_from_directory(os.path.join(UI, "assets"), f)

@app.get("/book/<slug>")
@app.get("/manage/<token>")
@app.get("/legacy")
def classic(slug=None, token=None): return send_from_directory("static", "index.html")  # public pages + classic admin UI

@app.get("/")
def spa(): return send_from_directory(UI, "index.html")

@app.errorhandler(404)
def nf(e): return err("Not found", 404) if request.path.startswith("/api") else send_from_directory(UI, "index.html")

if __name__ == "__main__": app.run(debug=True)
