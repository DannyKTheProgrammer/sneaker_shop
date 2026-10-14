# Наполнение учебной БД стартовыми данными каталога.
#
# Запуск: python -m app.seed
#
# Что важно в этом модуле:
#   * ID фиксированные (1, 2, 10, 20, 30, 101, 102, 201, 301), поэтому seed
#     можно запускать повторно и на него можно ссылаться в примерах.
#   * Повторный запуск ничего не дублирует и, главное, НЕ перезаписывает уже
#     изменённые цены и остатки: вставка делается через
#     INSERT ... ON CONFLICT (id) DO NOTHING. Существующая строка остаётся
#     ровно такой, какой её сделали после первого запуска.
#   * DO NOTHING срабатывает только на конфликт по id. Конфликт по другому
#     UNIQUE (например, бренд с новым id и уже занятым именем) — это ошибка
#     данных, и она должна быть видна как отказ, а не «проглочена» с
#     созданием неверной связи. Поэтому такое исключение не подавляется.
#   * Порядок вставки: бренды → товары → размеры (иначе FK не даст вставить).
#   * После фиксированных ID последовательности Identity сдвигаются вперёд,
#     чтобы следующие обычные INSERT не упали на конфликте первичного ключа.
#     Последовательность при этом никогда не двигается назад.
#   * Никаких пользователей, корзин, заказов и паролей: только каталог.
#     Таблицы не очищаются.

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Brand, Product, ProductSize

logger = logging.getLogger(__name__)


class SeedError(RuntimeError):
    """Seed не выполнен: БД осталась в исходном состоянии (откат сделан)."""


# Деньги — Decimal из строк: Decimal("7990.00") точен, а Decimal(7990.00)
# сначала проходит через float и может дать 7990.000000000001.
BRANDS = [
    {"id": 1, "name": "North"},
    {"id": 2, "name": "City"},
]

PRODUCTS = [
    {"id": 10, "name": "North Runner", "brand_id": 1, "price": Decimal("7990.00")},
    {"id": 20, "name": "City Walk", "brand_id": 2, "price": Decimal("5990.00")},
    {"id": 30, "name": "North Trail", "brand_id": 1, "price": Decimal("9990.00")},
]

PRODUCT_SIZES = [
    {"id": 101, "product_id": 10, "size": Decimal("42.0"), "stock": 3},
    {"id": 102, "product_id": 10, "size": Decimal("43.0"), "stock": 0},
    {"id": 201, "product_id": 20, "size": Decimal("41.0"), "stock": 5},
    {"id": 301, "product_id": 30, "size": Decimal("42.0"), "stock": 2},
]

# Таблицы, у которых id генерируется Identity: после фиксированных ID
# последовательность нужно подтянуть к максимуму.
IDENTITY_TABLES = ("brands", "products", "product_sizes")


def _insert_ignoring_existing_id(session: Session, model, rows: list[dict]) -> None:
    """Вставить строки, пропуская уже существующие id (ON CONFLICT (id) DO NOTHING).

    Конфликт по любому другому UNIQUE не подавляется: он означает, что в БД
    лежит строка с другим id и теми же данными, и продолжать нельзя.
    on_conflict_do_nothing есть только у postgresql-варианта insert,
    поэтому используется pg_insert, а не базовый sqlalchemy.insert.
    """
    if not rows:
        return
    stmt = (
        pg_insert(model.__table__)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)


def _sync_identity_sequences(session: Session, tables: tuple[str, ...]) -> None:
    """Сдвинуть последовательности Identity вперёд до максимума id.

    Делается двумя отдельными запросами: pg_get_serial_sequence() — это
    функция, возвращающая имя последовательности, и подставить её в FROM
    (как таблицу) нельзя. Вторым запросом вызывается setval уже от имени,
    полученного первым.

    GREATEST не даёт последовательности уехать НАЗАД: если она уже ушла
    дальше максимума (например, кто-то вставлял строки обычным способом),
    значение остаётся прежним.

    is_called=true означает, что следующее значение будет на единицу больше
    установленного (nextval вернёт max(id) + 1), а уже существующие id
    остаются валидными — текущие строки не затрагиваются.
    """
    for table in tables:
        sequence = session.execute(
            text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
        ).scalar_one()
        session.execute(
            text(
                "SELECT setval("
                f"  '{sequence}'::regclass,"
                f"  GREATEST((SELECT COALESCE(MAX(id), 1) FROM {table}),"
                f"           (SELECT last_value FROM {sequence})),"
                "  true)"
            )
        )


def _verify_expected_state(session: Session) -> None:
    """Проверить, что после вставки на месте все строки, которыми владеет seed.

    Зачем это поверх ON CONFLICT (id) DO NOTHING: DO NOTHING срабатывает
    на любой конфликт по id, даже если это ЧУЖАЯ строка с другим
    содержимым. Пример: в БД лежит бренд id=90 с именем 'North'. Вставка
    id=1 'North' конфликтует по UNIQUE(name), и скрипт мог бы молча
    пропустить её — тогда товары остались бы со ссылкой на несуществующий
    бренд id=1, то есть с неверной связью и без единой ошибки. Здесь такое
    состояние ловится и приводит к откату всей транзакции.

    Проверяются только «свои» поля: цена товара и остаток размера —
    изменяемые данные, их правят после первого запуска, и seed не должен
    считать такие правки ошибкой. Имя бренда и привязка товара к бренду,
    размер к товару — структурные связи, они должны совпадать.
    """
    for model, rows, owned_fields in (
        (Brand, BRANDS, ("name",)),
        (Product, PRODUCTS, ("name", "brand_id")),
        (ProductSize, PRODUCT_SIZES, ("product_id",)),
    ):
        ids = [row["id"] for row in rows]
        found = {
            obj.id: obj
            for obj in session.query(model).filter(model.id.in_(ids)).all()
        }
        for row in rows:
            obj = found.get(row["id"])
            if obj is None:
                raise SeedError(
                    f"{model.__tablename__}: строка id={row['id']} отсутствует "
                    "после вставки — конфликт по другому UNIQUE, который "
                    "нельзя пропустить. Связи могли бы стать неверными."
                )
            for field in owned_fields:
                expected = row[field]
                actual = getattr(obj, field)
                if actual != expected:
                    raise SeedError(
                        f"{model.__tablename__} id={row['id']}: поле {field} "
                        f"равно {actual!r}, а seed ожидает {expected!r}. "
                        "Похоже, в БД уже есть другая строка с этим id; "
                        "seed ничего не менял."
                    )


def seed(session: Session) -> None:
    """Выполнить весь seed в одной транзакции.

    Любая ошибка (в том числе конфликт по не-id UNIQUE) приводит к откату:
    база не остаётся в половинчатом состоянии.
    """
    try:
        _insert_ignoring_existing_id(session, Brand, BRANDS)
        _insert_ignoring_existing_id(session, Product, PRODUCTS)
        _insert_ignoring_existing_id(session, ProductSize, PRODUCT_SIZES)
        _verify_expected_state(session)
        _sync_identity_sequences(session, IDENTITY_TABLES)
    except IntegrityError as exc:
        session.rollback()
        raise SeedError(
            "Seed отклонён: конфликт по уникальности, который нельзя пропустить.\n"
            f"Детали: {exc.orig}\n"
            "Скорее всего в БД уже есть строка с другим id и такими же "
            "данными (например, бренд с занятым именем). Seed ничего не менял."
        ) from exc
    except SeedError:
        # Проверка согласованности не прошла: свои детали уже в сообщении,
        # но транзакцию откатить нужно здесь.
        session.rollback()
        raise
    except SQLAlchemyError as exc:
        session.rollback()
        raise SeedError(f"Seed не выполнен из-за ошибки БД: {exc}") from exc

    session.commit()


def describe(session: Session) -> None:
    """Показать, что получилось: это же используется как проверка."""
    print("-- brands --")
    for brand in session.query(Brand).order_by(Brand.id):
        print(f"  {brand.id} | {brand.name}")
    print("-- products --")
    for product in session.query(Product).order_by(Product.id):
        print(f"  {product.id} | {product.name} | brand_id={product.brand_id} | {product.price}")
    print("-- product_sizes --")
    for size in session.query(ProductSize).order_by(ProductSize.id):
        print(
            f"  {size.id} | product_id={size.product_id} | "
            f"size={size.size} | stock={size.stock}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed учебного каталога (идемпотентно).")
    parser.add_argument(
        "--describe",
        action="store_true",
        help="после вставки напечатать содержимое трёх таблиц",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    with SessionLocal() as session:
        try:
            seed(session)
        except SeedError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 1
        if args.describe:
            describe(session)

    logger.info(
        "Seed выполнен: 2 бренда, 3 товара, 4 размера; существующие строки не изменялись"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
