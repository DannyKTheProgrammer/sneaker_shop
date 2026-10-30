# Единица работы: одна операция = одна свежая сессия = одна транзакция.
# Сервисы (app/services/shop.py) входят в `transaction()` и сами не коммитят.

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import ShopConflict
from app.database import SessionLocal

# SQLSTATE прерванной транзакции, которую клиент может повторить:
# 40P01 deadlock, 40001 serialization_failure, 55P03 lock_timeout, 57014 statement_timeout.
RETRYABLE_SQLSTATES = frozenset({"40P01", "40001", "55P03", "57014"})

# Текст 409 для прерванной транзакции; сервер сам не повторяет.
CONCURRENT_RETRY_DETAIL = "concurrent_change_retry"

# SET LOCAL таймауты: действуют до конца транзакции и не протекают в пул.
OWNER_LOCK_TIMEOUT_MS = 3000
STATEMENT_TIMEOUT_MS = 5000


class ConcurrentChangeConflict(ShopConflict):
    """Транзакцию прервала конкуренция (409); ничего не зафиксировано, клиент может повторить.

    Сервер не повторяет сам: при обрыве соединения исход неизвестен.
    """

    detail = CONCURRENT_RETRY_DETAIL


def _sqlstate(exc: BaseException) -> str | None:
    """Достать SQLSTATE из исключения драйвера или None."""
    orig = getattr(exc, "orig", None)
    candidates = (orig, getattr(orig, "diag", None), getattr(orig, "__cause__", None))
    for candidate in candidates:
        if candidate is None:
            continue
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str) and len(value) == 5:
                return value
    return None


def _rollback_quietly(session: Session) -> None:
    """Откатить сессию, не заслоняя исходную ошибку."""
    with suppress(Exception):
        session.rollback()


@contextmanager
def transaction() -> Iterator[Session]:
    """Открыть свежую сессию с транзакцией READ COMMITTED на одну бизнес-операцию.

    Commit при нормальном выходе, rollback при исключении, close всегда.
    SQLSTATE из RETRYABLE_SQLSTATES превращаются в ConcurrentChangeConflict,
    остальные ошибки пробрасываются как есть.
    """
    session = SessionLocal()
    try:
        session.begin()
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        session.execute(text(f"SET LOCAL lock_timeout = '{OWNER_LOCK_TIMEOUT_MS}ms'"))
        session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
        yield session
    except DBAPIError as exc:
        _rollback_quietly(session)
        if _sqlstate(exc) in RETRYABLE_SQLSTATES:
            raise ConcurrentChangeConflict() from exc
        raise
    except BaseException:
        _rollback_quietly(session)
        raise
    else:
        try:
            session.commit()
        except DBAPIError as exc:
            _rollback_quietly(session)
            if _sqlstate(exc) in RETRYABLE_SQLSTATES:
                raise ConcurrentChangeConflict() from exc
            raise
    finally:
        session.close()
