import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db import Base, get_db
from app.models import User, Lesson, Cycle, now
from app.config import settings
from app.security import local_limits
from app.domain import grant


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db: yield db
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    def dependency(): yield db
    app.dependency_overrides[get_db] = dependency
    local_limits.clear()
    cfg = settings()
    monkeypatch.setattr(cfg, "gc_webhook_secret", "unit-test-callback-secret")
    monkeypatch.setattr(cfg, "kinescope_username", "drm")
    monkeypatch.setattr(cfg, "kinescope_password", "unit-test-drm-password")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def account(client, db):
    response = client.post("/api/auth/register", json={"name":"Анна", "email":"anna@example.com", "password":"long-password-987", "timezone":"Europe/Moscow", "consent":True})
    assert response.status_code == 200, response.text
    data = response.json(); user = db.get(User, data["user"]["id"])
    return user, {"Authorization":"Bearer " + data["access_token"]}


@pytest.fixture
def content(db):
    rows=[]
    for c in (1,2,3):
        for d in range(1,22):
            for kind in ("short","full"):
                lesson=Lesson(cycle_number=c,day=d,kind=kind,title="День "+str(d),tip="Совет",video_id=f"video-{c}-{d}-{kind}",duration=360 if kind=="short" else 1500,published=True)
                db.add(lesson);rows.append(lesson)
    db.commit();return rows


@pytest.fixture
def access(db,account,content):
    user,headers=account
    cycle=grant(db,user,"base");db.commit()
    lesson=next(l for l in content if l.cycle_number==1 and l.day==1 and l.kind=="short")
    return user,headers,cycle,lesson
