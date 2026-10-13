"""initial: семь таблиц магазина (M1)

Revision ID: 201c8afcccba
Revises:
Create Date: 2026-10-13 16:41:43.102898

Первая (и пока единственная) ревизия: создаёт brands, products, product_sizes,
users, cart_items, orders, order_items.

Проверено вручную по заданию:
  * ровно 7 create_table, включая products.image_url (nullable) и
    products.description (server_default='');
  * 7 внешних ключей, все NOT NULL, ни у одного нет ondelete (история
    заказов не удаляется каскадом);
  * составные UNIQUE: uq_product_size (product_id, size)
    и uq_cart_size (user_id, product_size_id);
  * именованные CHECK: ck_products_price, ck_size_positive, ck_stock,
    ck_cart_quantity, ck_order_status, ck_order_total, ck_item_quantity,
    ck_item_price;
  * nullable и server_default: description '', is_admin false,
    created_at now(), status 'created'.

Имена FK-ограничений заданы явно (<таблица>_<колонка>_fkey). Autogenerate их
не проставляет, и тогда PostgreSQL придумывает имена сам; при явных именах
падение FK в логе и в тексте ошибки называет понятное ограничение, а не
автосгенерированное. Имена CHECK/UNIQUE/PK сняты с моделей.

Порядок: brands и users создаются первыми (на них ссылаются products,
orders, cart_items), order_items — последним. downgrade идёт строго
в обратном порядке, чтобы не уронить FK.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '201c8afcccba'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Явные имена FK: PostgreSQL иначе сгенерирует их сам.
FK_NAMES = {
    ("orders", "user_id"): "orders_user_id_fkey",
    ("products", "brand_id"): "products_brand_id_fkey",
    ("product_sizes", "product_id"): "product_sizes_product_id_fkey",
    ("cart_items", "user_id"): "cart_items_user_id_fkey",
    ("cart_items", "product_size_id"): "cart_items_product_size_id_fkey",
    ("order_items", "order_id"): "order_items_order_id_fkey",
    ("order_items", "product_size_id"): "order_items_product_size_id_fkey",
}


def upgrade() -> None:
    # 1) brands — на неё ссылается products
    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # 2) users — на неё ссылаются orders и cart_items
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    # 3) orders — зависит от users
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('created', 'processing', 'completed')",
            name="ck_order_status",
        ),
        sa.CheckConstraint("total >= 0", name="ck_order_total"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=FK_NAMES[("orders", "user_id")]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 4) products — зависит от brands
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price > 0", name="ck_products_price"),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name=FK_NAMES[("products", "brand_id")]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 5) product_sizes — зависит от products
    op.create_table(
        "product_sizes",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("size", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.CheckConstraint("size > 0", name="ck_size_positive"),
        sa.CheckConstraint("stock >= 0", name="ck_stock"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=FK_NAMES[("product_sizes", "product_id")],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "size", name="uq_product_size"),
    )
    # 6) cart_items — зависит от users и product_sizes
    op.create_table(
        "cart_items",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_size_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_cart_quantity"),
        sa.ForeignKeyConstraint(
            ["product_size_id"],
            ["product_sizes.id"],
            name=FK_NAMES[("cart_items", "product_size_id")],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=FK_NAMES[("cart_items", "user_id")]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "product_size_id", name="uq_cart_size"
        ),
    )
    # 7) order_items — зависит от orders и product_sizes
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_size_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "price_at_purchase", sa.Numeric(precision=10, scale=2), nullable=False
        ),
        sa.CheckConstraint("price_at_purchase > 0", name="ck_item_price"),
        sa.CheckConstraint("quantity > 0", name="ck_item_quantity"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=FK_NAMES[("order_items", "order_id")]
        ),
        sa.ForeignKeyConstraint(
            ["product_size_id"],
            ["product_sizes.id"],
            name=FK_NAMES[("order_items", "product_size_id")],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # Строго обратный порядок: сначала таблицы, которые ссылаются на другие.
    op.drop_table("order_items")
    op.drop_table("cart_items")
    op.drop_table("product_sizes")
    op.drop_table("products")
    op.drop_table("orders")
    op.drop_table("users")
    op.drop_table("brands")
