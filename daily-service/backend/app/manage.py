import argparse
import getpass
from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import User, Lesson
from .security import passwords, check_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "admin"])
    parser.add_argument("--email")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == "seed":
            tips = ["Проведите следующий созвон стоя.", "Поставьте экран на уровне глаз.", "Между рабочими задачами встаньте и пройдитесь.", "Проверьте, удобно ли вашим плечам.", "Сделайте паузу и спокойно подышите.", "Поставьте обе стопы на пол.", "Выберите время для завтрашней разминки."]
            for c in range(1, 4):
                for day in range(1, 22):
                    for kind in ("short", "full"):
                        if not db.scalar(select(Lesson.id).where(Lesson.cycle_number == c, Lesson.day == day, Lesson.kind == kind)):
                            db.add(Lesson(cycle_number=c, day=day, kind=kind, title=("Мягкая разминка" if kind == "short" else "Полноценная тренировка") + f" · День {day}", tip=tips[(day-1)%len(tips)], duration=360 if kind == "short" else 1500, published=False))
            db.commit(); print("Созданы черновики 3 циклов. Добавьте видео и опубликуйте их через админку.")
        else:
            if not args.email: parser.error("--email required")
            password = getpass.getpass("Admin password (at least 10 characters): "); check_password(password)
            user = db.scalar(select(User).where(User.email == args.email.lower()))
            if not user: user = User(email=args.email.lower(), name="Администратор", password_hash=passwords.hash(password)); db.add(user)
            else: user.password_hash = passwords.hash(password)
            user.role = "admin"; user.verified = True; user.is_test = True; db.commit(); print("Администратор настроен")


if __name__ == "__main__": main()
