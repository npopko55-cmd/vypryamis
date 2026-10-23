import calendar
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, UTC, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import User, Cycle, FullAccess, Lesson, Progress, Event, Order, Outbox, PaymentEvent, now
from .config import settings


def timezone(value):
    try: return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError): return ZoneInfo("Europe/Moscow")


def local_today(user, at=None):
    value = at or datetime.now(UTC)
    if value.tzinfo is None: value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone(user.timezone)).date()


def plus_month(day):
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def event(db, user, kind, lesson_id=None, **data):
    db.add(Event(user_id=user.id, type=kind, local_day=local_today(user), lesson_id=lesson_id, data=data))


def enqueue(db, user, kind, key, **payload):
    if not db.scalar(select(Outbox.id).where(Outbox.key == key)):
        db.add(Outbox(user_id=user.id, kind=kind, key=key, payload=payload))


def cycles(db, user):
    return list(db.scalars(select(Cycle).where(Cycle.user_id == user.id, Cycle.revoked == False).order_by(Cycle.number)))


def active_access(db, user, today=None):
    day = today or local_today(user)
    return any(c.start <= day < c.access_end for c in cycles(db, user))


def ready_cycle(db, number, kind="short"):
    rows = list(db.scalars(select(Lesson).where(Lesson.cycle_number == number, Lesson.kind == kind, Lesson.published == True)))
    return len(rows) == 21 and all(l.video_id and l.duration >= settings().completion_seconds for l in rows)


def grant(db, user, product, order=None, today=None):
    # Lock the user before assigning cycle numbers or extending addon access.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    day = today or local_today(user)
    if product == "base":
        all_cycles = list(db.scalars(select(Cycle).where(Cycle.user_id == user.id).order_by(Cycle.number)))
        last = all_cycles[-1] if all_cycles else None
        start = max(day, last.end) if last else day
        number = last.number + 1 if last else 1
        row = Cycle(user_id=user.id, number=number, start=start, end=start + timedelta(days=21), access_end=plus_month(start), order_id=order.id if order else None)
    else:
        if not active_access(db, user, day): raise HTTPException(409, "Сначала продлите основной доступ")
        latest = db.scalar(select(FullAccess).where(FullAccess.user_id == user.id, FullAccess.revoked == False).order_by(FullAccess.end.desc()))
        start = max(day, latest.end) if latest else day
        row = FullAccess(user_id=user.id, start=start, end=start + timedelta(days=21), order_id=order.id if order else None)
    db.add(row); db.flush()
    event(db, user, "renewal" if product == "base" and row.number > 1 else "purchase", product=product)
    return row


def lesson_access(db, user, lesson, today=None, allow_trial=False):
    day = today or local_today(user)
    if not lesson or not lesson.published or not active_access(db, user, day): return False
    cycle = next((c for c in cycles(db, user) if c.number == lesson.cycle_number), None)
    if not cycle or cycle.start + timedelta(days=lesson.day - 1) > day: return False
    if lesson.kind == "short": return True
    paid = db.scalar(select(FullAccess.id).where(FullAccess.user_id == user.id, FullAccess.start <= day, FullAccess.end > day, FullAccess.revoked == False))
    return bool(paid or user.trial_lesson_id == lesson.id or (allow_trial and not user.trial_lesson_id))


def get_dashboard(db, user):
    day = local_today(user)
    all_cycles = cycles(db, user)
    active = active_access(db, user, day)
    current = next((c for c in reversed(all_cycles) if c.start <= day), None)
    all_lessons = list(db.scalars(select(Lesson).where(Lesson.published == True).order_by(Lesson.cycle_number, Lesson.day)))
    progress = {p.lesson_id: p for p in db.scalars(select(Progress).where(Progress.user_id == user.id))}
    calendar_days = []
    for c in all_cycles:
        short = {l.day: l for l in all_lessons if l.cycle_number == c.number and l.kind == "short"}
        full = {l.day: l for l in all_lessons if l.cycle_number == c.number and l.kind == "full"}
        for n in range(1, 22):
            date_value = c.start + timedelta(days=n - 1)
            lesson = short.get(n)
            p = progress.get(lesson.id) if lesson else None
            full_lesson = full.get(n)
            calendar_days.append({"date": date_value.isoformat(), "number": (c.number - 1) * 21 + n, "day": n, "cycle": c.number, "lesson_id": lesson.id if lesson else None, "title": lesson.title if lesson else "Разминка готовится", "tip": lesson.tip if lesson else "", "duration": lesson.duration if lesson else 360, "completed": bool(p and p.completed_at), "seconds": p.seconds if p else 0, "locked": not active or date_value > day or not lesson, "full_id": full_lesson.id if full_lesson else None, "full_unlocked": bool(full_lesson and lesson_access(db, user, full_lesson, day)), "full_trial": bool(full_lesson and not user.trial_lesson_id and active and date_value <= day)})
    selected = next((d for d in calendar_days if d["date"] == day.isoformat()), None)
    if not selected: selected = next((d for d in reversed(calendar_days) if d["date"] <= day.isoformat()), None)
    completed_dates = {e.local_day for e in db.scalars(select(Event).where(Event.user_id == user.id, Event.type == "completed"))}
    streak = 0; cursor = day if day in completed_dates else day - timedelta(days=1)
    while cursor in completed_dates: streak += 1; cursor -= timedelta(days=1)
    return {"user": {"id": user.id, "name": user.name, "email": user.email, "reminders": user.reminders, "verified": user.verified, "role": user.role, "timezone": user.timezone}, "today": day.isoformat(), "access": active, "access_end": max((c.access_end for c in all_cycles), default=None), "cycle": current.number if current else 0, "current_day": min(21, (day - current.start).days + 1) if current else 0, "completed": sum(bool(p.completed_at) for p in progress.values() if any(l.id == p.lesson_id and l.kind == "short" for l in all_lessons)), "streak": streak, "calendar": calendar_days, "selected": selected["lesson_id"] if selected else None, "completion_seconds": settings().completion_seconds, "base_price": settings().base_price, "full_price": settings().full_price}


def process_payment(db, data, source="callback"):
    provider_id = str(data.get("order_id", ""))[:100]
    email = str(data.get("email", "")).strip().lower()[:320]
    status = str(data.get("status", "")).lower().replace("ё", "е")
    try:
        raw_amount = str(data.get("amount", "0")).replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("руб.", "").replace("₽", "").replace("RUB", "").strip()
        cents = Decimal(raw_amount) * 100
        amount = int(cents) if cents.is_finite() and cents == cents.to_integral_value() else 0
    except (ValueError, OverflowError, InvalidOperation): amount = 0
    row = PaymentEvent(provider_id=provider_id, email=email, status=status, amount=amount)
    db.add(row)
    order = db.scalar(select(Order).where(Order.provider_id == provider_id).with_for_update()) if provider_id else None
    if not order and provider_id and str(data.get("order_number", "")).startswith("VP-"):
        candidate = db.scalar(select(Order).where(Order.id == str(data["order_number"])[3:]).with_for_update())
        if candidate and (not candidate.provider_id or candidate.provider_id == provider_id):
            order = candidate; order.provider_id = provider_id
    if not order:
        row.note = "Не найден заказ сервиса. Требуется ручная привязка"; return "review"
    row.order_id = order.id
    user = db.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if status in {"refunded", "returned", "возврат", "отменен", "cancelled"}:
        if order.status == "paid":
            for model in (Cycle, FullAccess):
                for access in db.scalars(select(model).where(model.order_id == order.id)): access.revoked = True
            order.status = "refunded"; event(db, user, "refund", order_id=order.id)
        row.note = "Возврат обработан"; return order.status
    if status not in {"payed", "завершен"}:
        row.note = "Заказ ещё не завершён"; return "pending"
    if amount != order.amount or str(data.get("currency", "RUB")).upper() != "RUB":
        row.note = "Сумма или валюта не совпадает"; return "review"
    if order.status in {"paid", "refunded"}: row.note = "Повторное событие"; return order.status
    if order.product == "full" and not active_access(db, user):
        row.note = "Оплата получена после окончания основного доступа. Требуется решение администратора"; return "review"
    grant(db, user, order.product, order)
    order.status = "paid"; order.paid_at = now()
    row.note = "Доступ выдан: " + source
    enqueue(db, user, "welcome", "paid:" + order.id)
    return "paid"
