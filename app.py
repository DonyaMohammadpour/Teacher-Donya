import os
import math
import shutil
import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, date, timedelta
from functools import wraps

import jdatetime
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    if os.environ.get("RENDER"):
        raise RuntimeError("SECRET_KEY must be configured in the hosting environment.")
    secret_key = "local-development-only-change-before-deployment"
app.config["SECRET_KEY"] = secret_key
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///teacher_donya_v2.db")
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    display_name = db.Column(db.String(120), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class StudentProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    phone = db.Column(db.String(40), default="")
    level = db.Column(db.String(30), default="")
    goal = db.Column(db.String(250), default="")
    class_type = db.Column(db.String(30), default="خصوصی")
    schedule_note = db.Column(db.String(200), default="")
    billing_mode = db.Column(db.String(20), default="session")  # session / course
    session_fee = db.Column(db.Integer, default=0)
    package_sessions = db.Column(db.Integer, default=8)
    package_price = db.Column(db.Integer, default=0)
    sms_consent = db.Column(db.Boolean, nullable=False, default=False)
    student = db.relationship("User", foreign_keys=[user_id])

class Lesson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student_profile.id"), nullable=False)
    lesson_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.String(10), default="")
    topic = db.Column(db.String(200), default="")
    homework = db.Column(db.String(500), default="")
    status = db.Column(db.String(30), default="برنامه‌ریزی‌شده")
    duration_minutes = db.Column(db.Integer, nullable=False, default=60)
    student_profile = db.relationship("StudentProfile", foreign_keys=[student_id])

class SMSNotification(db.Model):
    __table_args__ = (db.UniqueConstraint("student_id", "trigger_payment_id", "notification_type", name="uq_sms_exhaustion_event"),)
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student_profile.id"), nullable=False)
    trigger_payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=False)
    notification_type = db.Column(db.String(40), nullable=False, default="credits_exhausted")
    phone = db.Column(db.String(40), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    provider_message_id = db.Column(db.String(80), default="")
    provider_status_text = db.Column(db.String(200), default="")
    error = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)
    student_profile = db.relationship("StudentProfile", foreign_keys=[student_id])

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student_profile.id"), nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(200), default="")
    method = db.Column(db.String(30), default="کارت‌به‌کارت")
    payment_type = db.Column(db.String(30), default="پرداخت شهریه")
    status = db.Column(db.String(20), default="approved")  # pending / approved / rejected
    submitted_by = db.Column(db.String(20), default="teacher")
    sessions_purchased = db.Column(db.Integer, nullable=True)
    student_profile = db.relationship("StudentProfile", foreign_keys=[student_id])

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def teacher_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "teacher":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def student_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "student":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def owned_student(student_id):
    p = db.session.get(StudentProfile, student_id)
    if not p or p.teacher_id != current_user.id:
        abort(404)
    return p

def jalali_to_date(value):
    value = (value or "").strip().replace("-", "/")
    parts = value.split("/")
    if len(parts) != 3:
        raise ValueError("فرمت تاریخ باید سال/ماه/روز باشد.")
    jy, jm, jd = map(int, parts)
    return jdatetime.date(jy, jm, jd).togregorian()

def jdate(d):
    return jdatetime.date.fromgregorian(date=d).strftime("%Y/%m/%d")

def jdate_label(d):
    jd = jdatetime.date.fromgregorian(date=d)
    names = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
    return f"{names[d.weekday()]} {jd.strftime('%Y/%m/%d')}"

app.jinja_env.globals["jdate"] = jdate

def session_rate(profile):
    if profile.billing_mode == "session":
        return float(profile.session_fee or 0)
    return (float(profile.package_price or 0) / int(profile.package_sessions or 1)) if profile.package_sessions else 0.0

def payment_session_count(payment, profile):
    """Explicit session count wins; legacy payments are estimated from their amount."""
    if payment.sessions_purchased is not None:
        return max(0, int(payment.sessions_purchased))
    rate = session_rate(profile)
    return int(payment.amount // rate) if rate > 0 else 0

def payment_allocations(profile):
    approved = (Payment.query.filter_by(student_id=profile.id, status="approved")
                .order_by(Payment.payment_date.asc(), Payment.id.asc()).all())
    completed_lessons = (Lesson.query.filter_by(student_id=profile.id, status="برگزار شد")
                         .order_by(Lesson.lesson_date.asc(), Lesson.id.asc()).all())
    remaining_to_allocate = len(completed_lessons)
    result = []
    for payment in approved:
        purchased = payment_session_count(payment, profile)
        used = min(purchased, remaining_to_allocate)
        remaining_to_allocate -= used
        result.append({"payment": payment, "purchased": purchased,
                       "used": used, "remaining": purchased-used,
                       "estimated": payment.sessions_purchased is None})
    return result

def finances(profile):
    completed = Lesson.query.filter_by(student_id=profile.id, status="برگزار شد").count()
    approved = Payment.query.filter_by(student_id=profile.id, status="approved").all()
    paid = sum(x.amount for x in approved)
    rate = session_rate(profile)
    accrued = round(completed * rate)
    balance = max(0, accrued - paid)
    unpaid_sessions = min(completed, math.ceil(balance / rate) if rate else completed)
    pending = Payment.query.filter_by(student_id=profile.id, status="pending").all()
    allocations = payment_allocations(profile)
    return {"completed": completed, "paid": paid, "accrued": accrued,
            "balance": balance, "unpaid_sessions": unpaid_sessions,
            "pending_amount": sum(x.amount for x in pending),
            "sessions_remaining": sum(x["remaining"] for x in allocations),
            "payment_allocations": allocations}

def normalize_iranian_mobile(value):
    """Normalize common Persian/Arabic digit and Iran country-code formats."""
    value = str(value or "").strip()
    digit_map = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    value = value.translate(digit_map)
    value = value.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if value.startswith("+98"):
        value = "0" + value[3:]
    elif value.startswith("0098"):
        value = "0" + value[4:]
    elif value.startswith("98") and len(value) == 12:
        value = "0" + value[2:]
    elif len(value) == 10 and value.startswith("9"):
        value = "0" + value
    if len(value) != 11 or not value.startswith("09") or not value.isdigit():
        return None
    return value


def kavenegar_send_sms(phone, message):
    """Send one SMS via Kavenegar. Real sending is opt-in via environment flags."""
    api_key = os.environ.get("KAVENEGAR_API_KEY", "").strip()
    sender = os.environ.get("KAVENEGAR_SENDER", "").strip()
    enabled = os.environ.get("SMS_ENABLED", "false").lower() in {"1", "true", "yes"}
    dry_run = os.environ.get("SMS_DRY_RUN", "true").lower() in {"1", "true", "yes"}

    if dry_run:
        return {"status": "dry_run", "provider_status_text": "حالت آزمایشی؛ پیام واقعی ارسال نشد."}
    if not enabled or not api_key:
        return {"status": "not_configured", "error": "ارسال واقعی فعال نیست یا کلید API تنظیم نشده است."}

    endpoint = f"https://api.kavenegar.com/v1/{urllib.parse.quote(api_key, safe='')}/sms/send.json"
    payload = {"receptor": phone, "message": message}
    if sender:
        payload["sender"] = sender
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={"User-Agent": "Teacher-Donya/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        return_info = result.get("return", {})
        if int(return_info.get("status", 0)) != 200:
            return {"status": "failed", "error": str(return_info.get("message", "خطای نامشخص سرویس پیامک"))[:500]}
        entries = result.get("entries") or []
        first = entries[0] if entries else {}
        return {"status": "sent", "provider_message_id": str(first.get("messageid", "")),
                "provider_status_text": str(first.get("statustext", "درخواست به سرویس پیامک تحویل شد."))[:200],
                "sent_at": datetime.utcnow()}
    except urllib.error.HTTPError as exc:
        detail = "پاسخ نامعتبر از سرویس پیامک"
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = str(body.get("return", {}).get("message", detail))
        except Exception:
            pass
        return {"status": "failed", "error": detail[:500]}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)[:500]}


def notify_if_credits_exhausted(profile):
    """Create one notification per exhausted approved payment cycle."""
    if not profile.sms_consent:
        return None
    phone = normalize_iranian_mobile(profile.phone)
    if not phone:
        return None
    allocations = payment_allocations(profile)
    purchased_total = sum(item["purchased"] for item in allocations)
    remaining_total = sum(item["remaining"] for item in allocations)
    if purchased_total <= 0 or remaining_total > 0:
        return None
    approved_payments = [item["payment"] for item in allocations if item["purchased"] > 0]
    if not approved_payments:
        return None
    trigger_payment = approved_payments[-1]
    existing = SMSNotification.query.filter_by(student_id=profile.id,
        trigger_payment_id=trigger_payment.id, notification_type="credits_exhausted").first()
    if existing:
        return existing

    message = ("سلام. جلسات آموزشی شما به پایان رسیده است. "
               "برای ادامه کلاس‌ها و تمدید جلسات، لطفاً پرداخت خود را انجام دهید. "
               "Teacher Donya")
    notification = SMSNotification(teacher_id=profile.teacher_id, student_id=profile.id,
        trigger_payment_id=trigger_payment.id, phone=phone, message=message, status="pending")
    db.session.add(notification)
    db.session.flush()
    result = kavenegar_send_sms(phone, message)
    notification.status = result.get("status", "failed")
    notification.provider_message_id = result.get("provider_message_id", "")
    notification.provider_status_text = result.get("provider_status_text", "")
    notification.error = result.get("error", "")
    notification.sent_at = result.get("sent_at")
    db.session.commit()
    return notification

def parse_time(value):
    return datetime.strptime(value, "%H:%M").time()

def lesson_interval(lesson):
    start = parse_time(lesson.start_time)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = start_minutes + int(lesson.duration_minutes or 60)
    return start_minutes, end_minutes

def find_lesson_conflict(teacher_id, lesson_date, start_time, duration_minutes, exclude_id=None):
    if not start_time:
        return None
    new_start = parse_time(start_time)
    start_minutes = new_start.hour * 60 + new_start.minute
    end_minutes = start_minutes + int(duration_minutes or 60)
    candidates = Lesson.query.filter_by(teacher_id=teacher_id, lesson_date=lesson_date).all()
    for other in candidates:
        if exclude_id is not None and other.id == exclude_id:
            continue
        if other.status == "لغو شد" or not other.start_time:
            continue
        try:
            old_start, old_end = lesson_interval(other)
        except ValueError:
            continue
        if start_minutes < old_end and old_start < end_minutes:
            student_name = other.student_profile.student.display_name
            return {"lesson": other, "student_name": student_name,
                    "start": other.start_time, "end": minutes_to_time(old_end)}
    return None

def minutes_to_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard" if current_user.role == "teacher" else "student_home"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    # Teacher accounts are provisioned privately; public sign-up is disabled.
    flash("ساخت حساب مدرس از طریق ثبت‌نام عمومی غیرفعال است.")
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "").strip().lower()).first()
        if u and u.check_password(request.form.get("password", "")):
            login_user(u)
            return redirect(url_for("dashboard" if u.role == "teacher" else "student_home"))
        flash("نام کاربری یا رمز عبور درست نیست.")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/dashboard")
@teacher_required
def dashboard():
    students = StudentProfile.query.filter_by(teacher_id=current_user.id).all()
    today = date.today()
    lessons = Lesson.query.filter_by(teacher_id=current_user.id, lesson_date=today).order_by(Lesson.start_time).all()
    pending = Payment.query.filter_by(teacher_id=current_user.id, status="pending").order_by(Payment.payment_date.desc()).all()
    sms_logs = SMSNotification.query.filter_by(teacher_id=current_user.id).order_by(SMSNotification.created_at.desc()).limit(10).all()
    totals = [(p, finances(p)) for p in students]
    debtors = [(p, f) for p, f in totals if f["balance"] > 0]
    return render_template("dashboard.html", students=students, lessons=lessons, pending=pending,
                           total_balance=sum(f["balance"] for _, f in totals),
                           pending_count=len(pending), today_label=jdate_label(today),
                           sms_logs=sms_logs, debtors=debtors)

@app.route("/students/new", methods=["GET", "POST"])
@teacher_required
def new_student():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        try:
            session_fee = int(request.form.get("session_fee") or 0)
            package_sessions = int(request.form.get("package_sessions") or 8)
            package_price = int(request.form.get("package_price") or 0)
        except ValueError:
            flash("مبلغ و تعداد جلسات باید عددی باشند.")
            return render_template("student_form.html")
        if not name or not username or len(password) < 8:
            flash("نام، نام کاربری و رمز حداقل ۸ کاراکتری لازم است.")
        elif User.query.filter_by(username=username).first():
            flash("این نام کاربری قبلاً استفاده شده است.")
        else:
            u = User(username=username, display_name=name, role="student", teacher_id=current_user.id)
            u.set_password(password); db.session.add(u); db.session.flush()
            p = StudentProfile(teacher_id=current_user.id, user_id=u.id,
                phone=request.form.get("phone","").strip(), level=request.form.get("level","").strip(),
                goal=request.form.get("goal","").strip(), class_type=request.form.get("class_type","خصوصی"),
                schedule_note=request.form.get("schedule_note","").strip(),
                billing_mode=request.form.get("billing_mode","session"),
                session_fee=session_fee, package_sessions=max(1, package_sessions), package_price=package_price,
                sms_consent=(request.form.get("sms_consent") == "on"))
            db.session.add(p); db.session.commit()
            flash("پرونده زبان‌آموز ساخته شد.")
            return redirect(url_for("student_detail", student_id=p.id))
    return render_template("student_form.html")

@app.route("/students/<int:student_id>")
@teacher_required
def student_detail(student_id):
    p = owned_student(student_id)
    lessons = Lesson.query.filter_by(student_id=p.id, teacher_id=current_user.id).order_by(Lesson.lesson_date.desc(), Lesson.start_time.desc()).all()
    payments = Payment.query.filter_by(student_id=p.id, teacher_id=current_user.id).order_by(Payment.payment_date.desc()).all()
    return render_template("student_detail.html", profile=p, student=p.student, lessons=lessons,
                           payments=payments, finance=finances(p), jdate=jdate)

@app.route("/students/<int:student_id>/lessons/new", methods=["POST"])
@teacher_required
def add_lesson(student_id):
    p = owned_student(student_id)
    try:
        d = jalali_to_date(request.form.get("lesson_date"))
    except Exception:
        flash("تاریخ شمسی معتبر نیست؛ نمونه: 1405/09/01")
        return redirect(url_for("student_detail", student_id=p.id))
    start_time = request.form.get("start_time", "").strip()
    try:
        duration = int(request.form.get("duration_minutes") or 60)
        if duration < 15 or duration > 240 or duration % 15:
            raise ValueError()
        if not start_time:
            raise ValueError()
        parsed_start = parse_time(start_time)
        if parsed_start.hour * 60 + parsed_start.minute + duration > 24 * 60:
            raise ValueError()
    except ValueError:
        flash("ساعت شروع و مدت کلاس معتبر نیست؛ مدت باید بین ۱۵ تا ۲۴۰ دقیقه و پایان کلاس قبل از نیمه‌شب باشد.")
        return redirect(url_for("student_detail", student_id=p.id))
    conflict = find_lesson_conflict(current_user.id, d, start_time, duration)
    if conflict:
        flash(f"تداخل زمانی: با کلاس {conflict['student_name']} از ساعت {conflict['start']} تا {conflict['end']} هم‌پوشانی دارد. جلسه ثبت نشد.")
        return redirect(url_for("student_detail", student_id=p.id))
    l = Lesson(teacher_id=current_user.id, student_id=p.id, lesson_date=d,
        start_time=start_time, duration_minutes=duration, topic=request.form.get("topic","").strip(),
        homework=request.form.get("homework","").strip(), status=request.form.get("status","برنامه‌ریزی‌شده"))
    db.session.add(l); db.session.commit()
    notify_if_credits_exhausted(p)
    flash("جلسه ثبت شد.")
    return redirect(url_for("student_detail", student_id=p.id))

@app.route("/lessons/<int:lesson_id>/edit", methods=["GET", "POST"])
@teacher_required
def edit_lesson(lesson_id):
    l = db.session.get(Lesson, lesson_id)
    if not l or l.teacher_id != current_user.id:
        abort(404)
    if request.method == "POST":
        try:
            l.lesson_date = jalali_to_date(request.form.get("lesson_date"))
        except Exception:
            flash("تاریخ شمسی معتبر نیست.")
            return render_template("lesson_edit.html", lesson=l, jalali=jdate(l.lesson_date))
        start_time = request.form.get("start_time", "").strip()
        try:
            duration = int(request.form.get("duration_minutes") or 60)
            if duration < 15 or duration > 240 or duration % 15 or not start_time:
                raise ValueError()
            parsed_start = parse_time(start_time)
            if parsed_start.hour * 60 + parsed_start.minute + duration > 24 * 60:
                raise ValueError()
        except ValueError:
            flash("ساعت یا مدت کلاس معتبر نیست.")
            return render_template("lesson_edit.html", lesson=l, jalali=jdate(l.lesson_date))
        conflict = find_lesson_conflict(current_user.id, l.lesson_date, start_time, duration, exclude_id=l.id)
        if conflict and request.form.get("status") != "لغو شد":
            flash(f"تداخل زمانی: با کلاس {conflict['student_name']} از ساعت {conflict['start']} تا {conflict['end']} هم‌پوشانی دارد. تغییرات ذخیره نشد.")
            return render_template("lesson_edit.html", lesson=l, jalali=jdate(l.lesson_date))
        l.start_time = start_time
        l.duration_minutes = duration
        l.topic = request.form.get("topic","").strip()
        l.homework = request.form.get("homework","").strip()
        l.status = request.form.get("status","برنامه‌ریزی‌شده")
        db.session.commit()
        notify_if_credits_exhausted(l.student_profile)
        flash("تغییرات جلسه ذخیره شد.")
        return redirect(url_for("student_detail", student_id=l.student_id))
    return render_template("lesson_edit.html", lesson=l, jalali=jdate(l.lesson_date))

@app.route("/students/<int:student_id>/payments/new", methods=["POST"])
@teacher_required
def add_payment(student_id):
    p = owned_student(student_id)
    try:
        d = jalali_to_date(request.form.get("payment_date"))
        amount = int(request.form.get("amount","0"))
        raw_sessions = request.form.get("sessions_purchased", "").strip()
        sessions_purchased = int(raw_sessions) if raw_sessions else None
        if amount <= 0 or (sessions_purchased is not None and sessions_purchased <= 0): raise ValueError()
    except Exception:
        flash("تاریخ، مبلغ یا تعداد جلسات پرداخت معتبر نیست.")
        return redirect(url_for("student_detail", student_id=p.id))
    db.session.add(Payment(teacher_id=current_user.id, student_id=p.id, payment_date=d,
        amount=amount, description=request.form.get("description","").strip(),
        method=request.form.get("method","کارت‌به‌کارت"),
        payment_type=request.form.get("payment_type","پرداخت شهریه"), status="approved", submitted_by="teacher",
        sessions_purchased=sessions_purchased))
    db.session.commit()
    notify_if_credits_exhausted(p)
    flash("پرداخت ثبت شد.")
    return redirect(url_for("student_detail", student_id=p.id))

@app.route("/payments/<int:payment_id>/<action>", methods=["POST"])
@teacher_required
def review_payment(payment_id, action):
    pay = db.session.get(Payment, payment_id)
    if not pay or pay.teacher_id != current_user.id or pay.status != "pending":
        abort(404)
    if action == "approve":
        pay.status = "approved"
        flash("پرداخت تأیید شد و در حساب زبان‌آموز ثبت شد.")
    elif action == "reject":
        pay.status = "rejected"
        flash("درخواست پرداخت رد شد.")
    else:
        abort(400)
    db.session.commit()
    notify_if_credits_exhausted(pay.student_profile)
    return redirect(url_for("dashboard"))

@app.route("/students/<int:student_id>/sms-consent", methods=["POST"])
@teacher_required
def update_sms_consent(student_id):
    p = owned_student(student_id)
    p.sms_consent = request.form.get("sms_consent") == "on"
    db.session.commit()
    if p.sms_consent:
        notify_if_credits_exhausted(p)
    flash("رضایت دریافت پیامک به‌روزرسانی شد.")
    return redirect(url_for("student_detail", student_id=p.id))


@app.route("/sms-notifications/<int:notification_id>/retry", methods=["POST"])
@teacher_required
def retry_sms_notification(notification_id):
    notification = db.session.get(SMSNotification, notification_id)
    if not notification or notification.teacher_id != current_user.id:
        abort(404)
    if notification.status == "sent":
        flash("این پیام قبلاً به سرویس پیامک ارسال شده است.")
        return redirect(url_for("dashboard"))
    result = kavenegar_send_sms(notification.phone, notification.message)
    notification.status = result.get("status", "failed")
    notification.provider_message_id = result.get("provider_message_id", "")
    notification.provider_status_text = result.get("provider_status_text", "")
    notification.error = result.get("error", "")
    notification.sent_at = result.get("sent_at")
    db.session.commit()
    flash("وضعیت تلاش مجدد: " + notification.status)
    return redirect(url_for("dashboard"))


@app.route("/me")
@student_required
def student_home():
    p = StudentProfile.query.filter_by(user_id=current_user.id, teacher_id=current_user.teacher_id).first_or_404()
    lessons = Lesson.query.filter_by(student_id=p.id).order_by(Lesson.lesson_date.desc()).all()
    payments = Payment.query.filter_by(student_id=p.id).order_by(Payment.payment_date.desc()).all()
    return render_template("student_home.html", profile=p, lessons=lessons, payments=payments,
                           finance=finances(p), jdate=jdate)

@app.route("/me/payment", methods=["POST"])
@student_required
def student_payment_request():
    p = StudentProfile.query.filter_by(user_id=current_user.id, teacher_id=current_user.teacher_id).first_or_404()
    try:
        d = jalali_to_date(request.form.get("payment_date"))
        amount = int(request.form.get("amount","0"))
        if amount <= 0: raise ValueError()
    except Exception:
        flash("تاریخ یا مبلغ پرداخت معتبر نیست.")
        return redirect(url_for("student_home"))
    db.session.add(Payment(teacher_id=p.teacher_id, student_id=p.id, payment_date=d,
        amount=amount, description=request.form.get("description","").strip(),
        method=request.form.get("method","کارت‌به‌کارت"),
        payment_type="درخواست ثبت پرداخت", status="pending", submitted_by="student"))
    db.session.commit()
    flash("درخواست پرداخت برای تأیید مدرس ارسال شد.")
    return redirect(url_for("student_home"))

@app.route("/calendar")
@teacher_required
def calendar_view():
    raw = request.args.get("date")
    try:
        selected = jalali_to_date(raw) if raw else date.today()
    except Exception:
        selected = date.today()
    # Saturday-start week
    offset = (selected.weekday() + 2) % 7
    week_start = selected - timedelta(days=offset)
    week_days = [week_start + timedelta(days=i) for i in range(7)]
    weekly = (Lesson.query.filter(Lesson.teacher_id == current_user.id,
        Lesson.lesson_date >= week_start, Lesson.lesson_date <= week_start + timedelta(days=6))
        .order_by(Lesson.lesson_date, Lesson.start_time).all())
    day_names = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
    hours = list(range(7, 23))
    grid = []
    for hour in hours:
        cells = []
        for day in week_days:
            cell_lessons = []
            for lesson in weekly:
                if lesson.lesson_date != day or not lesson.start_time or lesson.status == "لغو شد":
                    continue
                try:
                    st, en = lesson_interval(lesson)
                except ValueError:
                    continue
                if st < (hour + 1) * 60 and en > hour * 60:
                    cell_lessons.append({"lesson": lesson, "starts_here": hour == st // 60,
                                         "end_time": minutes_to_time(en)})
            cells.append(cell_lessons)
        grid.append({"hour": f"{hour:02d}:00", "cells": cells})
    daily = [x for x in weekly if x.lesson_date == selected]
    return render_template("calendar.html", selected=selected, selected_j=jdate(selected),
        selected_label=jdate_label(selected), daily=daily, week_days=week_days,
        day_names=day_names, grid=grid,
        prev_j=jdate(week_start-timedelta(days=7)), next_j=jdate(week_start+timedelta(days=7)))

def migrate_schema():
    """Back up SQLite first, then add columns in-place without dropping existing rows."""
    inspector = db.inspect(db.engine)
    tables = set(inspector.get_table_names())
    lesson_cols = {c["name"] for c in inspector.get_columns("lesson")} if "lesson" in tables else set()
    payment_cols = {c["name"] for c in inspector.get_columns("payment")} if "payment" in tables else set()
    profile_cols = {c["name"] for c in inspector.get_columns("student_profile")} if "student_profile" in tables else set()
    needs_migration = (("lesson" in tables and "duration_minutes" not in lesson_cols) or
                       ("payment" in tables and "sessions_purchased" not in payment_cols) or
                       ("student_profile" in tables and "sms_consent" not in profile_cols))
    if needs_migration and db.engine.dialect.name == "sqlite":
        db_path_value = db.engine.url.database
        if db_path_value:
            db_path = Path(db_path_value)
            if db_path.exists():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = db_path.with_name(f"{db_path.stem}_backup_before_v3_{stamp}{db_path.suffix}")
                shutil.copy2(db_path, backup_path)
                print(f"Database backup created: {backup_path}")
    if "lesson" in tables and "duration_minutes" not in lesson_cols:
        db.session.execute(db.text("ALTER TABLE lesson ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 60"))
    if "payment" in tables and "sessions_purchased" not in payment_cols:
        db.session.execute(db.text("ALTER TABLE payment ADD COLUMN sessions_purchased INTEGER"))
    if "student_profile" in tables and "sms_consent" not in profile_cols:
        db.session.execute(db.text("ALTER TABLE student_profile ADD COLUMN sms_consent BOOLEAN NOT NULL DEFAULT 0"))
    db.session.commit()

@app.cli.command("init-db")
def init_db():
    db.create_all()
    migrate_schema()
    print("Database initialized and safely migrated.")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        migrate_schema()
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"}
    app.run(debug=debug_mode)
