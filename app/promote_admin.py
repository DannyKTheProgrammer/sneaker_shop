# Выдать роль администратора зарегистрированному пользователю.
# Запуск: python -m app.promote_admin --email admin@example.com
# Публичного маршрута для этого нет намеренно: роль выдаёт тот, кто имеет доступ к БД.

import argparse
import sys

from sqlalchemy import update

from app.database import SessionLocal
from app.models import User


def promote(email: str) -> int:
    """Выдать роль; 0 — роль выдана или уже была, 1 — пользователя нет."""
    normalized = email.strip().lower()
    with SessionLocal() as session:
        with session.begin():
            user = session.query(User).filter(User.email == normalized).one_or_none()
            if user is None:
                print(f"Пользователь {normalized} не найден: сначала POST /auth/register",
                      file=sys.stderr)
                return 1
            if user.is_admin is True:
                print(f"{normalized} уже администратор; ничего не изменено.")
                return 0
            session.execute(update(User).where(User.id == user.id).values(is_admin=True))
    print(f"{normalized} теперь администратор (id={user.id}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Выдать роль администратора по email.")
    parser.add_argument("--email", required=True, help="email зарегистрированного пользователя")
    args = parser.parse_args()
    return promote(args.email)


if __name__ == "__main__":
    raise SystemExit(main())
