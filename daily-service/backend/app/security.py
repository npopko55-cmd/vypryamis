import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
import jwt
import redis
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import User, LoginSession, now

passwords = PasswordHasher()
dummy_hash = passwords.hash(secrets.token_urlsafe(30))
local_limits = defaultdict(deque)


def digest(value: str): return hashlib.sha256(value.encode()).hexdigest()


def limit(key: str, count=10, seconds=60):
    key = "limit:" + digest(key)
    if settings().production:
        try:
            client = redis.Redis.from_url(settings().redis_url)
            hits = client.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n", 1, key, seconds)
        except redis.RedisError:
            raise HTTPException(503, "Сервис временно недоступен. Попробуйте позже")
    else:
        q = local_limits[key]
        while q and q[0] <= time.monotonic() - seconds: q.popleft()
        q.append(time.monotonic())
        hits = len(q)
    if hits > count: raise HTTPException(429, "Слишком много попыток. Попробуйте позже", headers={"Retry-After": str(seconds)})


def check_password(password: str):
    if len(password) < 10 or len(password) > 128:
        raise HTTPException(422, "Пароль должен содержать от 10 до 128 символов")
    if password.lower() in {"1234567890", "qwerty12345", "password123", "йцукен12345"}:
        raise HTTPException(422, "Придумайте менее распространённый пароль")


def verify_password(value, hashed):
    try: return passwords.verify(hashed, value)
    except (VerifyMismatchError, InvalidHashError): return False


def encode(user_id: str, session_id: str, audience="access", **extra):
    stamp = now()
    return jwt.encode({"sub": user_id, "sid": session_id, "aud": audience, "iss": "vypryamis", "iat": stamp, "exp": stamp + timedelta(minutes=15 if audience == "access" else 60), **extra}, settings().secret_key, algorithm="HS256")


def decode(token, audience="access"):
    try:
        return jwt.decode(token, settings().secret_key, algorithms=["HS256"], audience=audience, issuer="vypryamis", options={"require": ["sub", "sid", "exp", "iat", "aud", "iss"]})
    except jwt.PyJWTError: raise HTTPException(401, "Войдите в аккаунт")


def set_cookie(response: Response, value):
    response.set_cookie("refresh_token", value, max_age=settings().session_days * 86400, httponly=True, secure=settings().production, samesite="lax", path="/api/auth")


def new_session(db, user, response):
    token = secrets.token_urlsafe(48)
    row = LoginSession(user_id=user.id, token_hash=digest(token), expires_at=now() + timedelta(days=settings().session_days))
    db.add(row); db.flush(); db.commit()
    set_cookie(response, token)
    return {"access_token": encode(user.id, row.id)}


def current_user(request: Request, db: Session = Depends(get_db)):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "): raise HTTPException(401, "Войдите в аккаунт")
    claims = decode(authorization[7:])
    session = db.get(LoginSession, claims["sid"])
    user = db.get(User, claims["sub"])
    if not user or user.disabled or not session or session.revoked or session.user_id != user.id or session.expires_at <= now():
        raise HTTPException(401, "Войдите в аккаунт")
    return user


def admin_user(user=Depends(current_user)):
    if user.role != "admin": raise HTTPException(403, "Недостаточно прав")
    return user
