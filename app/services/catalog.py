# Чтение каталога из БД (M2). Только SELECT: ни вставок, ни изменений.
#
# Здесь сознательно нет ни FastAPI, ни HTTP-ошибок: сервис отвечает на
# вопрос «что лежит в БД», а решение «404 или пустой массив» принимает
# роутер. Так сервис можно переиспользовать и тестировать без HTTP.

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Brand, Product


def list_brands(session: Session) -> list[Brand]:
    """Все бренды, отсортированные по id.

    Сортировка задана явно: порядок строк в PostgreSQL без ORDER BY
    не определён, и ответ мог бы «прыгать» между запросами.
    """
    return list(session.scalars(select(Brand).order_by(Brand.id)))


def list_products(session: Session) -> list[Product]:
    """Все товары с размерами, отсортированные по id.

    selectinload(Product.sizes) — ключевая деталь. Без него обращение
    к product.sizes при сериализации сделало бы ОТДЕЛЬНЫЙ запрос на каждый
    товар (проблема N+1: 1 запрос за товарами + N за размерами). С
    selectinload размеры всех найденных товаров подгружаются одним
    дополнительным запросом с IN (...) — итого два запроса независимо от
    количества товаров.
    """
    stmt = (
        select(Product)
        .options(selectinload(Product.sizes))
        .order_by(Product.id)
    )
    return list(session.scalars(stmt))


def get_product(session: Session, product_id: int) -> Product | None:
    """Один товар с размерами или None, если такого id нет.

    None, а не исключение: сервис не знает, что для HTTP это 404.
    """
    stmt = (
        select(Product)
        .options(selectinload(Product.sizes))
        .where(Product.id == product_id)
    )
    return session.scalars(stmt).first()
