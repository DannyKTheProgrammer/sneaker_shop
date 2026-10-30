"""initial: семь таблиц магазина (M1)

Revision ID: 201c8afcccba
Revises:
Create Date: 2026-10-13 16:41:43.102898

Создаёт все семь таблиц; downgrade удаляет их в обратном порядке.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '201c8afcccba'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Явные имена FK (иначе PostgreSQL придумает их сам).
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
    op.create_table(
        "brands",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
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
    # Сначала таблицы, которые ссылаются на другие.
    op.drop_table("order_items")
    op.drop_table("cart_items")
    op.drop_table("product_sizes")
    op.drop_table("products")
    op.drop_table("orders")
    op.drop_table("users")
    op.drop_table("brands")
