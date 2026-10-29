# Единица работы (unit of work) для бизнес-операций корзины и заказов.
#
# Одна операция = одна свежая сессия = одна транзакция с одним владельцем
# commit/rollback. Сервисы (app/services/shop.py) входят в `transaction()`
# и сами не коммитят; роутеры сессию не трогают вовсе.

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import ShopConflict
from app.database import SessionLocal

# SQLSTATE, означающие «транзакцию прервали, работу можно безопасно повторить»:
#   40P01 deadlock_detected        — PostgreSQL выбрал нас жертвой циклa;
#   40001 serialization_failure    — конфликт сериализации (на READ COMMITTED
#                                    редок, но бывает при SSI/повторах);
#   55P03 lock_not_available       — сработал наш собственный lock_timeout;
#   57014 query_canceled           — сработал statement_timeout.
RETRYABLE_SQLSTATES = frozenset({"40P01", "40001", "55P03", "57014"})

# Текст ответа для 409, когда транзакцию прервали. Именно эта строка уходит
# клиенту: он может повторить запрос сам (но сервер НЕ повторяет его скрыто).
CONCURRENT_RETRY_DETAIL = "concurrent_change_retry"

# Локальные (SET LOCAL) таймауты транзакции сервиса. Они действуют только до
# конца транзакции и не протекают в соседние запросы, взявшие то же соединение
# из пула. Значения выбраны так: lock_timeout (3 c) — ожидание замка владельца
# или строки размера; statement_timeout (5 c) — общий предел одного оператора.
OWNER_LOCK_TIMEOUT_MS = 3000
STATEMENT_TIMEOUT_MS = 5000


class ConcurrentChangeConflict(ShopConflict):
    """Транзакцию прервала конкуренция: deadlock, конфликт, таймаут (409).

    Отдаёт `{"detail": "concurrent_change_retry"}` и означает: ничего не
    зафиксировано, клиент может повторить операцию сам. Сервер НЕ повторяет
    её автоматически: при обрыве соединения исход неизвестен, и повтор мог бы
    создать второй заказ.
    """

    detail = CONCURRENT_RETRY_DETAIL


def _sqlstate(exc: BaseException) -> str | None:
    """SQLSTATE из исключения драйвера (psycopg кладёт его в разные места).

    Приоритет: `orig.sqlstate`, затем `orig.diag.sqlstate`, затем
    `orig.pgcode`/`__cause__.sqlstate`. Возвращает None, если код не найден —
    тогда ошибка не будет замаскирована под «повторите».
    """
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
    """Откатить, не скрывая исходную ошибку.

    rollback сам может упасть (например, соединение уже разорвано), но это
    не повод потерять настоящее исключение: его пробросит вызывающий код.
    """
    with suppress(Exception):
        session.rollback()


@contextmanager
def transaction() -> Iterator[Session]:
    """Новая SessionLocal().begin() на одну бизнес-операцию корзины или checkout.

    Зачем нужен отдельный менеджер, а не `with session.begin()` в роутере:

    1. **Autobegin конфликт.** Сессия, в которой аутентификация прочитала
       `User`, уже в открытой транзакции (SQLAlchemy 2.0 начинает её неявно —
       autobegin). Вызвать в ней `session.begin()` нельзя: будет
       `InvalidRequestError`. Поэтому сервис открывает СВОЮ сессию
       (`SessionLocal()`) и работает в ней; из auth-слоя приходит только
       проверенный `user.id`, а короткая auth-сессия к этому моменту уже
       закрыта (`get_current_user_id`, дефект D1).
    2. **Изоляция задаётся ДО чтений.** `SET TRANSACTION ISOLATION LEVEL
       READ COMMITTED` — первый оператор после явного `begin()` (begin() не
       отправляет SQL), поэтому ни одно чтение не успевает пройти раньше.
    3. **Локальные таймауты.** `SET LOCAL lock_timeout`/`statement_timeout`
       действуют до конца транзакции и не остаются на соединении в пуле.
    4. **Один владелец commit/rollback.** Commit — на нормальном выходе,
       rollback — при любом исключении; `close()` — всегда, в finally. Auth
       сессия при этом не коммитится и не «спасает» операцию.
    5. **Повторяемые ошибки — отдельный 409.** SQLSTATE 40P01/40001/55P03/57014
       (deadlock, serialization, сработавший lock_timeout, statement_timeout)
       сначала откатываются, затем превращаются в `ConcurrentChangeConflict`
       (409 `concurrent_change_retry`). Все прочие ошибки (включая
       `IntegrityError` и ошибки в коде) пробрасываются как есть и не
       маскируются под «повторите».
    """
    session = SessionLocal()
    try:
        session.begin()  # явный begin, транзакция ещё не начата (сессия новая)
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        session.execute(text(f"SET LOCAL lock_timeout = '{OWNER_LOCK_TIMEOUT_MS}ms'"))
        session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
        yield session
    except DBAPIError as exc:
        # Сюда попадают и ошибки от flush (например, UNIQUE): откатываем, но
        # маскируем только те, что в RETRYABLE_SQLSTATES.
        _rollback_quietly(session)
        if _sqlstate(exc) in RETRYABLE_SQLSTATES:
            raise ConcurrentChangeConflict() from exc
        raise
    except BaseException:
        # ShopError и любые программные ошибки: откат и проброс без маскировки.
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
        # Сессия закрывается всегда: соединение возвращается в пул, а если
        # транзакция осталась открытой (например, после ошибки) — откатывается.
        session.close()
