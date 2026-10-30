# Наполнение БД стартовым каталогом. Запуск: python -m app.seed
# Идемпотентно: ID фиксированные, вставка через ON CONFLICT (id) DO NOTHING,
# существующие цены и остатки не перезаписываются. Таблицы не очищаются.

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


# Decimal из строк, не из float.
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

IDENTITY_TABLES = ("brands", "products", "product_sizes")


def _insert_ignoring_existing_id(session: Session, model, rows: list[dict]) -> None:
    """Вставить строки, пропуская существующие id.

    Конфликт по другому UNIQUE не подавляется.
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
    """Сдвинуть последовательности Identity до максимума id, чтобы обычные INSERT не конфликтовали.

    Имя последовательности берётся первым запросом (в FROM его не подставить);
    GREATEST не двигает её назад.
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
    """Проверить, что строки seed на месте и с теми же структурными полями.

    Ловит чужую строку с тем же id или именем. Цены и остатки не сверяются:
    их правят после первого запуска.
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
    """Выполнить seed одной транзакцией; любая ошибка откатывает её."""
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
        session.rollback()
        raise
    except SQLAlchemyError as exc:
        session.rollback()
        raise SeedError(f"Seed не выполнен из-за ошибки БД: {exc}") from exc

    session.commit()


def describe(session: Session) -> None:
    """Напечатать содержимое таблиц seed."""
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
