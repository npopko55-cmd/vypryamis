from contextlib import asynccontextmanager
from datetime import timedelta
import secrets
import math
from collections import Counter
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import Base, engine, get_db
from .models import *
from .security import current_user, admin_user, limit, passwords, verify_password, check_password, new_session, digest, set_cookie, encode, decode, dummy_hash
from .domain import local_today, timezone, event, enqueue, cycles, active_access, ready_cycle, grant, lesson_access, get_dashboard, process_payment
from .getcourse import create_checkout, ProviderError


@asynccontextmanager
async def lifespan(app):
    if not settings().production: Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Выпрямись API", version="1.0.0", lifespan=lifespan, docs_url=None if settings().production else "/docs")


@app.middleware("http")
async def headers(request: Request, call_next):
    origin = request.headers.get("origin")
    allowed_origins = {settings().public_url.rstrip("/")}
    if not settings().production: allowed_origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin.rstrip("/") not in allowed_origins:
        return JSONResponse(status_code=403, content={"detail": "Недопустимый источник запроса"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    if settings().production: response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=100)
    timezone: str = "Europe/Moscow"
    consent: bool
    reminders: bool = True


class EmailRequest(BaseModel): email: EmailStr
class TokenRequest(BaseModel): token: str = Field(max_length=500)
class ResetRequest(TokenRequest): password: str = Field(max_length=128)
class ChangePassword(BaseModel):
    current_password: str = Field(max_length=128)
    password: str = Field(max_length=128)
class Preference(BaseModel): reminders: bool
class Checkout(BaseModel): product: str = Field(pattern="^(base|full)$")
class WatchRequest(BaseModel): lesson_id: str
class ChunkRequest(BaseModel):
    sequence: int = Field(ge=0, le=100000)
    seconds: float = Field(ge=0, le=20, allow_inf_nan=False)


def user_out(user):
    return {"id": user.id, "email": user.email, "name": user.name, "timezone": user.timezone, "role": user.role, "reminders": user.reminders, "verified": user.verified}


def email_token(db, user, purpose):
    token = secrets.token_urlsafe(32)
    db.add(EmailToken(user_id=user.id, token_hash=digest(token), purpose=purpose, expires_at=now() + timedelta(minutes=30 if purpose == "reset" else 1440)))
    enqueue(db, user, purpose, purpose + ":" + digest(token), token=token)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@app.get("/api/config")
def public_config():
    cfg = settings()
    return {"base_price": cfg.base_price, "full_price": cfg.full_price, "support_email": cfg.support_email, "legal_name": cfg.legal_name, "legal_inn": cfg.legal_inn, "checkout_configured": bool(cfg.gc_account_url and cfg.gc_api_key and cfg.gc_base_offer), "reminder_hour": cfg.reminder_hour}


@app.post("/api/auth/register")
def register(body: Registration, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    limit("register:" + ip, 10); limit("register-day:" + ip, 300, 86400)
    if not body.consent: raise HTTPException(422, "Нужно согласие на обработку персональных данных")
    check_password(body.password)
    email = str(body.email).lower().strip()
    if db.scalar(select(User.id).where(User.email == email)): raise HTTPException(409, "Для этой почты уже есть аккаунт. Войдите или восстановите пароль")
    user = User(email=email, name=body.name.strip(), password_hash=passwords.hash(body.password), timezone=str(timezone(body.timezone)), reminders=body.reminders)
    db.add(user)
    try: db.flush()
    except IntegrityError: db.rollback(); raise HTTPException(409, "Аккаунт уже существует")
    email_token(db, user, "verify")
    event(db, user, "registered")
    return {**new_session(db, user, response), "user": user_out(user)}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    limit("login-ip:" + ip, 40); limit("login:" + str(body.email).lower() + ":" + ip, 10, 300)
    user = db.scalar(select(User).where(User.email == str(body.email).lower().strip()))
    valid = verify_password(body.password, user.password_hash if user else dummy_hash)
    if not user or not valid or user.disabled: raise HTTPException(401, "Неверная почта или пароль")
    event(db, user, "login")
    return {**new_session(db, user, response), "user": user_out(user)}


@app.post("/api/auth/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    limit("refresh:" + (request.client.host if request.client else "unknown"), 600)
    row = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(request.cookies.get("refresh_token", ""))).with_for_update())
    user = db.get(User, row.user_id) if row else None
    if not row or row.revoked or row.expires_at <= now() or not user or user.disabled:
        raise HTTPException(401, "Войдите в аккаунт")
    token = secrets.token_urlsafe(48)
    row.token_hash = digest(token); row.expires_at = now() + timedelta(days=settings().session_days)
    db.commit(); set_cookie(response, token)
    return {"access_token": encode(user.id, row.id), "user": user_out(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    row = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(request.cookies.get("refresh_token", ""))))
    if row: row.revoked = True; db.commit()
    response.delete_cookie("refresh_token", path="/api/auth")
    return {"ok": True}


@app.post("/api/auth/logout-all")
def logout_all(response: Response, user=Depends(current_user), db: Session = Depends(get_db)):
    for row in db.scalars(select(LoginSession).where(LoginSession.user_id == user.id)): row.revoked = True
    db.commit(); response.delete_cookie("refresh_token", path="/api/auth")
    return {"ok": True}


@app.post("/api/auth/forgot")
def forgot(body: EmailRequest, request: Request, db: Session = Depends(get_db)):
    limit("forgot:" + (request.client.host if request.client else "unknown"), 5, 300)
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    if user: email_token(db, user, "reset"); db.commit()
    return {"message": "Если аккаунт существует, письмо для восстановления отправлено"}


@app.post("/api/auth/reset")
def reset(body: ResetRequest, db: Session = Depends(get_db)):
    check_password(body.password)
    token = db.scalar(select(EmailToken).where(EmailToken.token_hash == digest(body.token), EmailToken.purpose == "reset").with_for_update())
    if not token or token.used or token.expires_at <= now(): raise HTTPException(400, "Ссылка устарела. Запросите новую")
    token.used = True
    user = db.get(User, token.user_id); user.password_hash = passwords.hash(body.password)
    for row in db.scalars(select(LoginSession).where(LoginSession.user_id == user.id)): row.revoked = True
    db.commit(); return {"ok": True}


@app.post("/api/auth/verify")
def verify(body: TokenRequest, db: Session = Depends(get_db)):
    token = db.scalar(select(EmailToken).where(EmailToken.token_hash == digest(body.token), EmailToken.purpose == "verify").with_for_update())
    if not token or token.used or token.expires_at <= now(): raise HTTPException(400, "Ссылка устарела. Запросите новую")
    db.get(User, token.user_id).verified = True; token.used = True; db.commit()
    return {"ok": True}


@app.post("/api/auth/resend-verification")
def resend(user=Depends(current_user), db: Session = Depends(get_db)):
    limit("verify:" + user.id, 3, 3600)
    if not user.verified: email_token(db, user, "verify"); db.commit()
    return {"ok": True}


@app.post("/api/auth/change-password")
def change_password(body: ChangePassword, request: Request, user=Depends(current_user), db: Session = Depends(get_db)):
    limit("password:" + user.id, 5, 300)
    if not verify_password(body.current_password, user.password_hash): raise HTTPException(401, "Неверный текущий пароль")
    check_password(body.password); user.password_hash = passwords.hash(body.password)
    current = decode(request.headers["authorization"][7:])["sid"]
    for row in db.scalars(select(LoginSession).where(LoginSession.user_id == user.id, LoginSession.id != current)): row.revoked = True
    db.commit(); return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)): return user_out(user)


@app.patch("/api/me")
def preferences(body: Preference, user=Depends(current_user), db: Session = Depends(get_db)):
    user.reminders = body.reminders; db.commit(); return user_out(user)


@app.get("/api/dashboard")
def dashboard(user=Depends(current_user), db: Session = Depends(get_db)):
    today = local_today(user)
    if not db.scalar(select(Event.id).where(Event.user_id == user.id, Event.type == "visit", Event.local_day == today)):
        event(db, user, "visit"); db.commit()
    return get_dashboard(db, user)


@app.get("/api/orders")
def orders(user=Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": o.id, "product": o.product, "amount": o.amount, "status": o.status, "created_at": o.created_at, "paid_at": o.paid_at} for o in db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()))]


@app.post("/api/checkout")
def checkout(body: Checkout, user=Depends(current_user), db: Session = Depends(get_db)):
    limit("checkout:" + user.id, 5, 300)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    if body.product == "base":
        all_cycles = list(db.scalars(select(Cycle).where(Cycle.user_id == user.id).order_by(Cycle.number)))
        next_number = all_cycles[-1].number + 1 if all_cycles else 1
        if not ready_cycle(db, next_number): raise HTTPException(409, "Новый цикл готовится. Открываем продажу после загрузки всех 21 разминок")
    else:
        if not active_access(db, user): raise HTTPException(409, "Сначала продлите основной доступ")
        current = next((c for c in reversed(cycles(db, user)) if c.start <= local_today(user)), None)
        if not current or not ready_cycle(db, current.number, "full"): raise HTTPException(409, "Полные тренировки этого цикла ещё готовятся")
    row = db.scalar(select(Order).where(Order.user_id == user.id, Order.product == body.product, Order.status == "pending", Order.created_at > now() - timedelta(hours=24)).order_by(Order.created_at.desc()))
    if not row:
        row = Order(user_id=user.id, product=body.product, amount=settings().base_price if body.product == "base" else settings().full_price)
        db.add(row); db.flush()
    db.commit()
    if row.payment_url: return {"order_id": row.id, "url": row.payment_url}
    try: provider_id, number, url = create_checkout(row, user)
    except ProviderError as exc: raise HTTPException(503, str(exc)) from None
    row.provider_id = provider_id; row.provider_number = number; row.payment_url = url
    db.commit(); return {"order_id": row.id, "url": url}


@app.get("/api/orders/{order_id}")
def order_status(order_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Order, order_id)
    if not row or row.user_id != user.id: raise HTTPException(404, "Заказ не найден")
    return {"id": row.id, "status": row.status, "paid_at": row.paid_at}


@app.post("/api/getcourse/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    secret = settings().gc_webhook_secret
    if not secret or not secrets.compare_digest(request.headers.get("x-webhook-secret", ""), secret): raise HTTPException(403, "Forbidden")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > 16384: raise HTTPException(413, "Payload too large")
    import json
    from urllib.parse import parse_qs
    try:
        if "json" in request.headers.get("content-type", ""): data = json.loads(raw)
        else: data = {k: v[-1] for k, v in parse_qs(raw.decode()).items()}
        if not isinstance(data, dict) or not data.get("order_id"): raise ValueError()
    except (ValueError, UnicodeError): raise HTTPException(400, "Invalid payload")
    result = process_payment(db, data); db.commit()
    return {"status": result}


@app.post("/api/watch/start")
def watch_start(body: WatchRequest, request: Request, user=Depends(current_user), db: Session = Depends(get_db)):
    limit("watch:" + user.id, 60)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    lesson = db.get(Lesson, body.lesson_id)
    if not lesson_access(db, user, lesson, allow_trial=True): raise HTTPException(403, "Эта тренировка пока недоступна")
    if not lesson.video_id: raise HTTPException(409, "Видео ещё готовится")
    full_paid = db.scalar(select(FullAccess.id).where(FullAccess.user_id == user.id, FullAccess.start <= local_today(user), FullAccess.end > local_today(user), FullAccess.revoked == False))
    if lesson.kind == "full" and not user.trial_lesson_id and not full_paid:
        user.trial_lesson_id = lesson.id; event(db, user, "full_trial", lesson.id)
    for row in db.scalars(select(WatchSession).where(WatchSession.user_id == user.id, WatchSession.active == True)): row.active = False
    watch = WatchSession(user_id=user.id, lesson_id=lesson.id)
    db.add(watch); db.flush()
    event(db, user, "video_start", lesson.id)
    # Separate audience prevents playback JWTs from authenticating API requests.
    sid = decode(request.headers["authorization"][7:])["sid"]
    jwt_token = encode(user.id, sid, "playback", lesson=lesson.id)
    db.commit()
    return {"watch_id": watch.id, "video_id": lesson.video_id, "url": "https://kinescope.io/embed/" + lesson.video_id + "?drmauthtoken=" + jwt_token}


@app.post("/api/watch/{watch_id}/chunk")
def watch_chunk(watch_id: str, body: ChunkRequest, user=Depends(current_user), db: Session = Depends(get_db)):
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    watch = db.scalar(select(WatchSession).where(WatchSession.id == watch_id).with_for_update())
    if not watch or watch.user_id != user.id: raise HTTPException(404, "Просмотр не найден")
    lesson = db.get(Lesson, watch.lesson_id)
    if not lesson_access(db, user, lesson): raise HTTPException(403, "Доступ закончился")
    progress = db.scalar(select(Progress).where(Progress.user_id == user.id, Progress.lesson_id == lesson.id).with_for_update())
    if not progress:
        progress = Progress(user_id=user.id, lesson_id=lesson.id, seconds=0); db.add(progress); db.flush()
    duplicate = db.scalar(select(WatchChunk.id).where(WatchChunk.watch_id == watch.id, WatchChunk.sequence == body.sequence))
    if not duplicate:
        if not watch.active: raise HTTPException(409, "Разминка запущена на другом устройстве. Запустите её здесь снова")
        elapsed = max(0, (now() - watch.last_seen).total_seconds())
        seconds = min(body.seconds, elapsed, 20)
        db.add(WatchChunk(watch_id=watch.id, sequence=body.sequence, seconds=seconds))
        progress.seconds = min(lesson.duration, progress.seconds + seconds)
        watch.last_seen = now()
        if not progress.completed_at and progress.seconds >= settings().completion_seconds and lesson.kind == "short":
            progress.completed_at = now(); event(db, user, "completed", lesson.id)
        db.commit()
    return {"seconds": progress.seconds, "completed": bool(progress.completed_at), "required": settings().completion_seconds}


class DRMRequest(BaseModel):
    id: str
    token: str
    type: str = "vod"


@app.post("/api/kinescope/authorize")
def drm(body: DRMRequest, request: Request, db: Session = Depends(get_db)):
    import base64
    cfg = settings()
    expected = "Basic " + base64.b64encode((cfg.kinescope_username + ":" + cfg.kinescope_password).encode()).decode()
    if not cfg.kinescope_password or not secrets.compare_digest(request.headers.get("authorization", ""), expected): raise HTTPException(403)
    try: claims = decode(body.token, "playback")
    except HTTPException: raise HTTPException(403)
    user = db.get(User, claims["sub"]); lesson = db.get(Lesson, claims.get("lesson"))
    session = db.get(LoginSession, claims["sid"])
    if not user or user.disabled or not session or session.revoked or session.user_id != user.id or session.expires_at <= now() or body.type != "vod" or not lesson or lesson.video_id != body.id or not lesson_access(db, user, lesson): raise HTTPException(403)
    return {"allowed": True}


@app.post("/api/unsubscribe")
def unsubscribe(body: TokenRequest, db: Session = Depends(get_db)):
    import jwt
    try: claims = jwt.decode(body.token, settings().secret_key, algorithms=["HS256"], audience="unsubscribe", issuer="vypryamis")
    except jwt.PyJWTError: raise HTTPException(400, "Ссылка недействительна")
    user = db.get(User, claims["sub"])
    if user: user.reminders = False; db.commit()
    return {"ok": True}


@app.get("/api/admin/stats")
def stats(admin=Depends(admin_user), db: Session = Depends(get_db)):
    users = list(db.scalars(select(User).where(User.role != "admin", User.is_test == False)))
    ids = [u.id for u in users]
    all_cycles = list(db.scalars(select(Cycle).where(Cycle.user_id.in_(ids), Cycle.revoked == False))) if ids else []
    first = {c.user_id: c for c in all_cycles if c.number == 1}
    events = list(db.scalars(select(Event).where(Event.user_id.in_(ids)))) if ids else []
    user_map = {u.id: u for u in users}
    cohort = []
    for n in range(1, 22):
        eligible = {uid: c for uid, c in first.items() if c.start + timedelta(days=n-1) <= local_today(user_map[uid])}
        visited = {e.user_id for e in events if e.user_id in eligible and e.type == "visit" and e.local_day == eligible[e.user_id].start + timedelta(days=n-1)}
        completed = {e.user_id for e in events if e.user_id in eligible and e.type == "completed" and e.local_day == eligible[e.user_id].start + timedelta(days=n-1)}
        cohort.append({"day": n, "eligible": len(eligible), "visited": len(visited), "completed": len(completed), "visit_rate": round(len(visited)*100/len(eligible), 1) if eligible else None, "completion_rate": round(len(completed)*100/len(eligible), 1) if eligible else None})
    paid = list(db.scalars(select(Order).where(Order.user_id.in_(ids), Order.status == "paid"))) if ids else []
    purchases = {o.user_id for o in paid if o.product == "base"}
    renewals = {c.user_id for c in all_cycles if c.number > 1}
    full = {o.user_id for o in paid if o.product == "full"}
    completions = [e for e in events if e.type == "completed"]
    launch_counts = Counter((e.user_id, e.local_day) for e in events if e.type == "video_start")
    repeat_users = {uid for (uid, day), count in launch_counts.items() if count > 1}
    lesson_map = {l.id: l for l in db.scalars(select(Lesson))}
    started = {e.user_id for e in events if e.type == "video_start" and e.lesson_id in lesson_map and lesson_map[e.lesson_id].kind == "short" and lesson_map[e.lesson_id].cycle_number == 1 and lesson_map[e.lesson_id].day == 1}
    daily = {}
    for e in events:
        if e.type == "visit": daily.setdefault(e.local_day.isoformat(), set()).add(e.user_id)
    return {"users": len(users), "buyers": len(purchases), "started": len(started & set(first)), "renewals": len(renewals), "full_buyers": len(full), "full_trials": len({e.user_id for e in events if e.type == "full_trial"}), "revenue": sum(o.amount for o in paid), "average_completed": round(len(completions)/len(first), 1) if first else 0, "retention": cohort, "daily": [{"date": d, "visits": len(v)} for d, v in sorted(daily.items())][-30:], "unique_30_days": len({e.user_id for e in events if e.type == "visit" and e.created_at >= now() - timedelta(days=30)}), "repeat_users": len(repeat_users)}


@app.get("/api/admin/users")
def admin_users(q: str = "", offset: int = 0, admin=Depends(admin_user), db: Session = Depends(get_db)):
    query = select(User).where(or_(User.email.ilike("%" + q[:100] + "%"), User.name.ilike("%" + q[:100] + "%"))).order_by(User.created_at.desc()).offset(max(0, offset)).limit(50)
    return [{**user_out(u), "created_at": u.created_at, "is_test": u.is_test, "disabled": u.disabled, "access": active_access(db, u)} for u in db.scalars(query)]


@app.get("/api/admin/users/{user_id}")
def admin_detail(user_id: str, admin=Depends(admin_user), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user: raise HTTPException(404)
    return {**get_dashboard(db, user), "is_test": user.is_test, "disabled": user.disabled, "orders": [{"id": o.id, "amount": o.amount, "product": o.product, "status": o.status} for o in db.scalars(select(Order).where(Order.user_id == user_id))], "events": [{"type": e.type, "date": e.local_day, "created_at": e.created_at, "lesson_id": e.lesson_id} for e in db.scalars(select(Event).where(Event.user_id == user_id).order_by(Event.created_at.desc()).limit(200))]}


class AdminUserEdit(BaseModel):
    reminders: bool | None = None
    is_test: bool | None = None
    disabled: bool | None = None


@app.patch("/api/admin/users/{user_id}")
def admin_edit(user_id: str, body: AdminUserEdit, admin=Depends(admin_user), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user: raise HTTPException(404)
    if user.id == admin.id and body.disabled: raise HTTPException(409, "Нельзя заблокировать себя")
    for k, v in body.model_dump(exclude_none=True).items(): setattr(user, k, v)
    db.add(Audit(actor_id=admin.id, action="user.update", target_id=user.id, details=body.model_dump(exclude_none=True)))
    db.commit(); return {"ok": True}


class ManualGrant(BaseModel):
    product: str = Field(pattern="^(base|full)$")
    reason: str = Field(min_length=5, max_length=500)


@app.post("/api/admin/users/{user_id}/grant")
def manual_grant(user_id: str, body: ManualGrant, admin=Depends(admin_user), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user: raise HTTPException(404)
    grant(db, user, body.product)
    db.add(Audit(actor_id=admin.id, action="access.grant", target_id=user_id, details=body.model_dump()))
    db.commit(); return {"ok": True}


@app.get("/api/admin/payments")
def payment_list(admin=Depends(admin_user), db: Session = Depends(get_db)):
    return [{"id": e.id, "provider_id": e.provider_id, "email": e.email, "status": e.status, "amount": e.amount, "note": e.note, "order_id": e.order_id, "created_at": e.created_at} for e in db.scalars(select(PaymentEvent).order_by(PaymentEvent.created_at.desc()).limit(100))]


class PaymentBind(BaseModel):
    user_id: str
    product: str = Field(pattern="^(base|full)$")
    reason: str = Field(min_length=5, max_length=500)


@app.post("/api/admin/payments/{event_id}/bind")
def bind_payment(event_id: str, body: PaymentBind, admin=Depends(admin_user), db: Session = Depends(get_db)):
    payment = db.scalar(select(PaymentEvent).where(PaymentEvent.id == event_id).with_for_update())
    user = db.get(User, body.user_id)
    if not payment or not user: raise HTTPException(404)
    existing = db.scalar(select(Order).where(Order.provider_id == payment.provider_id))
    if existing and existing.status == "paid": raise HTTPException(409, "Этот платёж уже использован")
    expected = settings().base_price if body.product == "base" else settings().full_price
    if payment.status not in {"payed", "завершен"} or payment.amount != expected: raise HTTPException(409, "Статус или сумма не соответствует тарифу")
    order = existing or Order(user_id=user.id, product=body.product, amount=expected, provider_id=payment.provider_id)
    if existing and existing.status == "refunded": raise HTTPException(409, "Возвращённый платёж нельзя использовать")
    order.user_id = user.id; order.product = body.product; db.add(order); db.flush()
    grant(db, user, body.product, order); order.status = "paid"; order.paid_at = now()
    payment.order_id = order.id; payment.note = "Привязан вручную: " + body.reason
    db.add(Audit(actor_id=admin.id, action="payment.bind", target_id=payment.id, details=body.model_dump()))
    enqueue(db, user, "welcome", "paid:" + order.id); db.commit(); return {"ok": True}


@app.get("/api/admin/lessons")
def lesson_list(admin=Depends(admin_user), db: Session = Depends(get_db)):
    return [{"id": l.id, "cycle_number": l.cycle_number, "day": l.day, "kind": l.kind, "title": l.title, "tip": l.tip, "video_id": l.video_id, "duration": l.duration, "published": l.published} for l in db.scalars(select(Lesson).order_by(Lesson.cycle_number, Lesson.day, Lesson.kind))]


class LessonEdit(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    tip: str = Field(max_length=2000)
    video_id: str = Field(max_length=100, pattern="^[a-zA-Z0-9_-]*$")
    duration: int = Field(ge=180, le=7200)
    published: bool
    @model_validator(mode="after")
    def valid_published(self):
        if self.published and not self.video_id: raise ValueError("Для публикации укажите ID видео")
        return self


@app.patch("/api/admin/lessons/{lesson_id}")
def lesson_edit(lesson_id: str, body: LessonEdit, admin=Depends(admin_user), db: Session = Depends(get_db)):
    lesson = db.get(Lesson, lesson_id)
    if not lesson: raise HTTPException(404)
    for k, v in body.model_dump().items(): setattr(lesson, k, v)
    db.add(Audit(actor_id=admin.id, action="lesson.update", target_id=lesson.id, details=body.model_dump()))
    db.commit(); return {"ok": True}


@app.get("/api/admin/integrations")
def integrations(admin=Depends(admin_user)):
    cfg = settings()
    return {"getcourse": bool(cfg.gc_account_url and cfg.gc_api_key and cfg.gc_webhook_secret), "base_offer": bool(cfg.gc_base_offer), "full_offer": bool(cfg.gc_full_offer), "mail": bool(cfg.mail_api_key and cfg.mail_sender), "video": bool(cfg.kinescope_username and cfg.kinescope_password), "reminder_hour": cfg.reminder_hour, "environment": cfg.environment}
