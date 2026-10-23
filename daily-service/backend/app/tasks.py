"""Durable mail outbox, local-time reminders and GetCourse reconciliation."""
import html
import logging
from datetime import datetime, timedelta, UTC
import jwt
import httpx
import redis
from celery import Celery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import SessionLocal
from .models import User, Cycle, Lesson, Progress, Outbox, now
from .domain import local_today, timezone, active_access, enqueue, process_payment
from .getcourse import start_export, poll_export, normalize_export, ProviderError

log = logging.getLogger(__name__)
celery = Celery("vypryamis", broker=settings().redis_url)
celery.conf.update(timezone="UTC", enable_utc=True, task_serializer="json", accept_content=["json"], worker_prefetch_multiplier=1, task_acks_late=True, beat_schedule={
    "mail-every-minute": {"task": "app.tasks.deliver_mail", "schedule": 60},
    "reminders-every-five-minutes": {"task": "app.tasks.schedule_reminders", "schedule": 300},
    "getcourse-hourly": {"task": "app.tasks.reconcile", "schedule": 3600},
})


@celery.task
def schedule_reminders():
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.disabled == False, User.reminders == True, User.verified == True)):
            local = datetime.now(UTC).astimezone(timezone(user.timezone)); day = local.date()
            if local.hour < settings().reminder_hour or not active_access(db, user, day): continue
            cycle = db.scalar(select(Cycle).where(Cycle.user_id == user.id, Cycle.start <= day, Cycle.end > day, Cycle.revoked == False).order_by(Cycle.number.desc()))
            if cycle:
                lesson = db.scalar(select(Lesson).where(Lesson.cycle_number == cycle.number, Lesson.day == (day-cycle.start).days+1, Lesson.kind == "short", Lesson.published == True))
                if not lesson: continue
                done = db.scalar(select(Progress.id).where(Progress.user_id == user.id, Progress.lesson_id == lesson.id, Progress.completed_at.is_not(None)))
                if not done: enqueue(db, user, "reminder", f"daily:{user.id}:{day}", lesson_id=lesson.id, day=day.isoformat())
            else:
                finished = db.scalar(select(Cycle).where(Cycle.user_id == user.id, Cycle.end == day, Cycle.revoked == False))
                if finished: enqueue(db, user, "renew", f"renew:{finished.id}", day=day.isoformat())
            try: db.commit()
            except IntegrityError: db.rollback()


def mail_content(row, user):
    root = settings().public_url.rstrip("/")
    token = row.payload.get("token", "")
    content = {
        "verify": ("Подтвердите почту · Выпрямись", "Остался один шаг", "Подтвердите почту, чтобы получать напоминания о разминках.", root + "/verify?token=" + token, "Подтвердить почту"),
        "reset": ("Восстановление пароля · Выпрямись", "Новый пароль", "Вы запросили восстановление пароля. Ссылка действует 30 минут.", root + "/reset?token=" + token, "Задать пароль"),
        "welcome": ("Ваша первая разминка уже ждёт", "Время для себя", "Оплата получена. Программа доступна в вашем кабинете.", root + "/app", "Открыть разминку"),
        "reminder": ("Ваша разминка на сегодня уже ждёт 🙌", "Несколько минут для себя", "Сегодня открылась новая разминка. Найдите удобное время и начните.", root + "/app", "Открыть разминку"),
        "renew": ("21 день позади · Продолжим?", "Продолжайте в своём ритме", "Ваш цикл завершён. В кабинете можно продлить доступ и открыть следующие 21 разминку.", root + "/app", "Открыть кабинет"),
    }
    subject, title, text, link, cta = content[row.kind]
    unsubscribe = jwt.encode({"sub": user.id, "aud": "unsubscribe", "iss": "vypryamis", "exp": now()+timedelta(days=365)}, settings().secret_key, algorithm="HS256")
    unsub_url = root + "/unsubscribe?token=" + unsubscribe
    body = f'<html lang="ru"><body style="margin:0;background:#f6f5f1;font-family:Arial,sans-serif;color:#1c2026"><div style="max-width:520px;margin:40px auto;padding:32px;background:white;border-radius:24px"><p style="color:#075bea;font-weight:bold">выпрямись.</p><h1>{html.escape(title)}</h1><p style="line-height:1.6">{html.escape(text)}</p><a href="{html.escape(link)}" style="display:inline-block;background:#075bea;color:white;padding:16px 24px;border-radius:16px;text-decoration:none">{cta}</a><p style="font-size:12px;margin-top:32px"><a href="{html.escape(unsub_url)}">Отключить напоминания</a></p></div></body></html>'
    return subject, body, unsub_url


@celery.task
def deliver_mail():
    if not settings().mail_api_key or not settings().mail_sender: return {"configured": False}
    delivered = 0
    for _ in range(50):
        with SessionLocal() as db:
            row = db.scalar(select(Outbox).where(Outbox.sent_at.is_(None), Outbox.next_attempt <= now(), Outbox.attempts < 8).order_by(Outbox.next_attempt).with_for_update(skip_locked=True).limit(1))
            if not row: break
            user = db.get(User, row.user_id)
            skip = not user or user.disabled
            if row.kind in {"reminder", "renew"}:
                skip = skip or not user.reminders or not user.verified or not active_access(db, user)
                if row.payload.get("day") != local_today(user).isoformat(): skip = True
            if row.kind == "reminder" and not skip:
                skip = bool(db.scalar(select(Progress.id).where(Progress.user_id == user.id, Progress.lesson_id == row.payload.get("lesson_id"), Progress.completed_at.is_not(None))))
            if skip: row.sent_at = now(); row.payload = {}; db.commit(); continue
            subject, body, unsubscribe = mail_content(row, user)
            row.attempts += 1
            try:
                response = httpx.post(settings().mail_api_url, headers={"X-API-KEY": settings().mail_api_key}, json={"message": {"recipients": [{"email": user.email}], "body": {"html": body}, "subject": subject, "from_email": settings().mail_sender, "from_name": "Выпрямись", "headers": {"List-Unsubscribe": "<"+unsubscribe+">"}}}, timeout=20)
                response.raise_for_status(); payload = response.json()
                if payload.get("status") != "success": raise ValueError("Provider rejected email")
                row.sent_at = now(); row.payload = {}; delivered += 1
            except (httpx.HTTPError, ValueError):
                row.next_attempt = now() + timedelta(minutes=min(360, 2 ** row.attempts))
                log.warning("Mail delivery failed for outbox %s (attempt %s)", row.id, row.attempts)
            db.commit()
    return {"delivered": delivered}


@celery.task
def reconcile():
    if not settings().gc_api_key: return {"configured": False}
    client = redis.Redis.from_url(settings().redis_url)
    if not client.set("gc:export-lock", "1", nx=True, ex=1800): return {"busy": True}
    try:
        export_id = start_export()
        if not export_id: raise ProviderError("No export ID")
        poll_reconciliation.apply_async(args=[str(export_id), 0], countdown=30)
    except Exception:
        client.delete("gc:export-lock")
        log.exception("Could not start GetCourse reconciliation")
        raise


@celery.task
def poll_reconciliation(export_id, attempt):
    client = redis.Redis.from_url(settings().redis_url)
    try:
        rows = poll_export(export_id)
        if rows is None:
            if attempt >= 15: raise ProviderError("Export timeout")
            poll_reconciliation.apply_async(args=[export_id, attempt+1], countdown=min(60, 20*(attempt+1))); return
        with SessionLocal() as db:
            for row in rows:
                normalized = normalize_export(row)
                # Shared GetCourse accounts contain other products: only our issued order numbers/IDs.
                from .models import Order
                if not db.scalar(select(Order.id).where(Order.provider_id == str(normalized["order_id"]))) and not str(normalized["order_number"]).startswith("VP-"): continue
                process_payment(db, normalized, "export"); db.commit()
        client.delete("gc:export-lock")
    except Exception:
        client.delete("gc:export-lock"); log.exception("GetCourse reconciliation failed"); raise
