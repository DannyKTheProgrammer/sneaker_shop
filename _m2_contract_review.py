# Independent M2 contract review (helper, not part of the application).
# Run:
#   1) start the API against a throwaway database, e.g.:
#        .\sneaker_shop_.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001
#      with DATABASE_URL pointing to that database (see _review_m01_probe.py --phase setup)
#   2) .\sneaker_shop_.venv\Scripts\python.exe -X utf8 _m2_contract_review.py --port 8001
#
# Checks EVERY public read against an independently built fixture:
#   * product 40 with THREE sizes (dup check), 42 with all-zero stock,
#     41 named "100% cotton" (literal % check), 43 named "w_ld" (literal _)
#   * an order referencing size 101 (for the delete conflict)
# Compares API output with direct SQL and rolls back everything it added.

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, Product, ProductSize, User

failures: list[str] = []
BASE = ""
DB = ""
ENGINE = None


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def http_request(url: str, method: str = "GET") -> tuple[int, object, str]:
    """Запрос по полному URL (для краевых query-строк вроде ?limit=)."""
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
            try:
                return response.status, json.loads(raw), raw
            except json.JSONDecodeError:
                return response.status, raw, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw), raw
        except json.JSONDecodeError:
            return exc.code, raw, raw


def call(method: str, path: str, params: dict | None = None) -> tuple[int, object, str]:
    url = BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    return http_request(url, method)


def api_ids(params: dict | None = None) -> list:
    code, body, _ = call("GET", "/products", params)
    if code != 200 or not isinstance(body, list):
        return [f"http={code}"]
    return [item["id"] for item in body]


def sql_ids(where: str = "", params: dict | None = None) -> list[int]:
    """Ожидаемый результат считаем независимым SQL, а не тем же кодом."""
    query = f"SELECT id FROM products {where} ORDER BY id"
    with Session(ENGINE) as session:
        return list(session.scalars(text(query), params or {}))


def main() -> int:
    global BASE, DB, ENGINE

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--database", default="sneaker_shop")
    args = parser.parse_args()
    BASE = f"http://127.0.0.1:{args.port}"
    DB = args.database
    ENGINE = create_engine(
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:55433/{DB}"
    )

    show(f"0. контекст: {BASE}, база {DB}")
    code, body, _ = call("GET", "/health")
    check("API отвечает", code == 200, f"http={code}")
    if code != 200:
        return 1

    with Session(ENGINE) as session:
        base_counts = {
            "brands": session.scalar(text("SELECT count(*) FROM brands")),
            "products": session.scalar(text("SELECT count(*) FROM products")),
            "product_sizes": session.scalar(text("SELECT count(*) FROM product_sizes")),
        }
        check("seed на месте (2/3/4)", base_counts == {"brands": 2, "products": 3, "product_sizes": 4},
              str(base_counts))

        # --- независимый фикстур -------------------------------------------
        show("1. фикстур: товар с тремя размерами, нулевым stock и буквальными знаками")
        session.add(Product(id=40, name="Multi Size", brand_id=1, price=Decimal("1000.00"),
                            description="", image_url=None))
        session.add(Product(id=42, name="Zero Stock", brand_id=1, price=Decimal("2000.00"),
                            description="", image_url=None))
        session.add(Product(id=41, name="100% cotton", brand_id=1, price=Decimal("3000.00"),
                            description="", image_url=None))
        session.add(Product(id=43, name="w_ld", brand_id=1, price=Decimal("4000.00"),
                            description="", image_url=None))
        session.flush()
        session.add_all([
            ProductSize(id=401, product_id=40, size=Decimal("42.0"), stock=1),
            ProductSize(id=402, product_id=40, size=Decimal("43.0"), stock=1),
            ProductSize(id=403, product_id=40, size=Decimal("44.0"), stock=1),
            ProductSize(id=421, product_id=42, size=Decimal("42.0"), stock=0),
            ProductSize(id=422, product_id=42, size=Decimal("43.0"), stock=0),
            ProductSize(id=411, product_id=41, size=Decimal("42.0"), stock=1),
            ProductSize(id=431, product_id=43, size=Decimal("42.0"), stock=1),
        ])
        user = User(email="m2-review@example.test", hashed_password="x")
        session.add(user)
        session.flush()
        order = Order(user_id=user.id, total=Decimal("100.00"), status="created")
        session.add(order)
        session.flush()
        session.add(OrderItem(order_id=order.id, product_size_id=401, quantity=1,
                              price_at_purchase=Decimal("1000.00")))
        session.commit()
        size_count_40 = session.scalar(text("SELECT count(*) FROM product_sizes WHERE product_id = 40"))
        check("у товара 40 три размера (для проверки дублей)", size_count_40 == 3, str(size_count_40))

        show("2. все публичные чтения отвечают и отдают ожидаемое")
        code, body, raw = call("GET", "/health")
        check("/health -> 200 {'status':'ok'}", code == 200 and body == {"status": "ok"}, f"{code} {raw}")
        code, body, _ = call("GET", "/brands")
        check("/brands -> 200, ids [1, 2]",
              code == 200 and [b["id"] for b in body] == [1, 2] and body[0]["name"] == "North",
              f"{code} {body}")
        code, body, _ = call("GET", "/products")
        check("/products -> 200, отсортирован по id",
              code == 200 and [p["id"] for p in body] == sorted(p["id"] for p in body),
              f"{code} {[p['id'] for p in body]}")
        for pid in (10, 20, 30, 40, 42, 43):
            code, body, _ = call("GET", f"/products/{pid}")
            check(f"/products/{pid} -> 200 с ключами контракта",
                  code == 200 and set(body) == {"id", "name", "brand_id", "price", "description",
                                                "image_url", "sizes"}, f"{code} {list(body) if isinstance(body, dict) else body}")
        code, body, _ = call("GET", "/products/40")
        check("/products/40 отдаёт все три размера", len(body["sizes"]) == 3,
              str([(s["id"], s["size"], s["stock"]) for s in body["sizes"]]))

        show("3. Decimal: цена и размер приходят строками, без потери точности")
        code, body, raw = call("GET", "/products/10")
        check("price — строка '7990.00'", body["price"] == "7990.00" and isinstance(body["price"], str),
              repr(body["price"]))
        check("size — строка '42.0'", body["sizes"][0]["size"] == "42.0",
              repr(body["sizes"][0]["size"]))
        check("в ответе нет float-артефактов", "7990.000000000001" not in raw, raw[:120])
        with Session(ENGINE) as s:
            db_price = s.scalar(text("SELECT price FROM products WHERE id = 10"))
        check("строка совпадает с типом numeric в БД", Decimal(body["price"]) == db_price,
              f"{body['price']} vs {db_price}")

        show("4. валидация границ: отказ раньше БД (422) и приём на границах")
        rejects = [
            ("limit=0", {"limit": 0}), ("limit=101", {"limit": 101}), ("offset=-1", {"offset": -1}),
            ("limit=abc", {"limit": "abc"}), ("limit=20.5", {"limit": "20.5"}),
            ("brand_id=0", {"brand_id": 0}), ("brand_id=-5", {"brand_id": -5}),
            ("brand_id=abc", {"brand_id": "abc"}),
            ("min_price=-1", {"min_price": "-1"}), ("min_price=NaN", {"min_price": "NaN"}),
            ("max_price=Infinity", {"max_price": "Infinity"}), ("min_price=10.999", {"min_price": "10.999"}),
            ("min_price=100000000", {"min_price": "100000000"}),
            ("size=0", {"size": "0"}), ("size=-1", {"size": "-1"}), ("size=1000", {"size": "1000"}),
            ("size=42.55", {"size": "42.55"}), ("size=abc", {"size": "abc"}),
            ("search 101 символ", {"search": "x" * 101}),
            ("min_price>max_price", {"min_price": "9000", "max_price": "1000"}),
            ("unknown param", {"unknown_param": "1"}),
            ("products/abc", None),
        ]
        for label, params in rejects:
            if label == "products/abc":
                code, _, _ = call("GET", "/products/abc")
            else:
                code, _, _ = call("GET", "/products", params)
            check(f"{label} -> 422", code == 422, f"http={code}")
        accepted = [("limit=1", {"limit": 1}), ("limit=100", {"limit": 100}), ("offset=0", {"offset": 0}),
                    ("min_price=0", {"min_price": "0"}), ("max_price=99999999.99", {"max_price": "99999999.99"}),
                    ("size=999", {"size": "999"}), ("size=42.5", {"size": "42.5"}),
                    ("search 100 символов", {"search": "x" * 100}),
                    ("min_price=max_price", {"min_price": "7990", "max_price": "7990"})]
        for label, params in accepted:
            code, _, _ = call("GET", "/products", params)
            check(f"{label} -> 200", code == 200, f"http={code}")

        show("5. фильтры: AND и независимая сверка с SQL")
        cases = [
            ("brand_id=1", {"brand_id": 1}, "WHERE brand_id = 1"),
            ("price 6000..8000", {"min_price": "6000", "max_price": "8000"},
             "WHERE price >= 6000 AND price <= 8000"),
            ("size=42 (только stock>0)", {"size": "42"},
             "WHERE EXISTS (SELECT 1 FROM product_sizes s WHERE s.product_id = products.id "
             "AND s.size = 42 AND s.stock > 0)"),
            ("brand_id=1 AND size=42 AND max_price=8000",
             {"brand_id": 1, "size": "42", "max_price": "8000"},
             "WHERE brand_id = 1 AND price <= 8000 AND EXISTS (SELECT 1 FROM product_sizes s "
             "WHERE s.product_id = products.id AND s.size = 42 AND s.stock > 0)"),
            ("size=43 (у товара 10 stock=0, у товара 40 stock=1)", {"size": "43"},
             "WHERE EXISTS (SELECT 1 FROM product_sizes s WHERE s.product_id = products.id "
             "AND s.size = 43 AND s.stock > 0)"),
            ("brand_id=999", {"brand_id": 999}, "WHERE brand_id = 999"),
            ("search=nOrTh", {"search": "nOrTh"}, "WHERE name ILIKE '%north%'"),
        ]
        for label, params, where in cases:
            got, expected = api_ids(params), sql_ids(where)
            check(f"{label}: API == SQL", got == expected, f"api={got} sql={expected}")

        show("6. правило наличия stock>0")
        code, body, _ = call("GET", "/products/42")
        check("товар 42 существует и у него два размера", code == 200 and len(body["sizes"]) == 2,
              f"{code} {[(s['size'], s['stock']) for s in body.get('sizes', [])]}")
        check("size=42 не возвращает товар 42 (весь stock = 0)", 42 not in api_ids({"size": "42"}),
              str(api_ids({"size": "42"})))
        check("size=43 не возвращает товар 10 (stock=0), но возвращает 40",
              api_ids({"size": "43"}) == sql_ids(
                  "WHERE EXISTS (SELECT 1 FROM product_sizes s WHERE s.product_id = products.id "
                  "AND s.size = 43 AND s.stock > 0)"),
              str(api_ids({"size": "43"})))

        show("7. буквальный поиск: % и _ не работают как шаблоны")
        # Товары: 41 = "100% cotton" (содержит %), 43 = "w_ld" (содержит _),
        # 42 = "Zero Stock", 10 = "North Runner" (ни того, ни другого).
        check("search='%' находит ТОЛЬКО товар с буквальным '%' (41)",
              api_ids({"search": "%"}) == [41], str(api_ids({"search": "%"})))
        check("search='_' находит ТОЛЬКО товар с буквальным '_' (43)",
              api_ids({"search": "_"}) == [43], str(api_ids({"search": "_"})))
        check("search='%' НЕ находит товары без '%' (иначе был бы wildcard)",
              10 not in api_ids({"search": "%"}) and 30 not in api_ids({"search": "%"}),
              str(api_ids({"search": "%"})))
        check("search='100%' находит товар 41 ('100% cotton')",
              api_ids({"search": "100%"}) == [41], str(api_ids({"search": "100%"})))
        check("search='0% c' находит 41 (подстрока с %)",
              api_ids({"search": "0% c"}) == [41], str(api_ids({"search": "0% c"})))
        check("search='w_ld' находит 43 по буквальному подчёркиванию",
              api_ids({"search": "w_ld"}) == [43], str(api_ids({"search": "w_ld"})))
        check("search='_' не находит товары без '_' (10, 30)",
              10 not in api_ids({"search": "_"}) and 30 not in api_ids({"search": "_"}),
              str(api_ids({"search": "_"})))
        check("search='cotton' находит 41 без учёта регистра",
              api_ids({"search": "COTTON"}) == [41], str(api_ids({"search": "COTTON"})))
        check("search с пробелами по краям находит 41",
              api_ids({"search": "  cotton  "}) == [41], str(api_ids({"search": "  cotton  "})))
        check("search из пробелов не сужает выдачу",
              len(api_ids({"search": "   "})) == 7, str(len(api_ids({"search": "   "}))))

        show("7b. разведка краевых query-случаев (фиксирую поведение, не требование)")
        for label, raw_query in [
            ("?limit= (пустое значение)", "limit="),
            ("?limit=+20 (пробел)", "limit=%2020"),
            ("?offset= (пустое)", "offset="),
            ("?brand_id= (пустое)", "brand_id="),
            ("?search= (пустое)", "search="),
            ("?search=a&search=b (дважды)", "search=a&search=b"),
        ]:
            code, body, _ = http_request(f"{BASE}/products?{raw_query}")
            print(f"    {label:32} -> http={code}, ids="
                  f"{[p['id'] for p in body] if code == 200 and isinstance(body, list) else body}")

        show("8. без дублей при нескольких размерах (EXISTS, а не JOIN)")
        got = api_ids({"size": "42"})
        check("size=42: товары 40/41/43 ровно по разу", got.count(40) == 1 and got.count(41) == 1
              and got.count(43) == 1, str(got))
        check("в выдаче нет повторяющихся id", len(got) == len(set(got)), str(got))
        code, body, _ = call("GET", "/products", {"size": "42"})
        check("в JSON нет двух элементов с одним id",
              len([p["id"] for p in body]) == len({p["id"] for p in body}), str([p["id"] for p in body]))

        show("9. стабильная сортировка перед пагинацией")
        full = api_ids({"limit": 100})
        pages = []
        offset = 0
        while True:
            page = api_ids({"limit": 2, "offset": offset})
            if not page:
                break
            pages.extend(page)
            offset += 2
        check("страницы по 2 покрывают всю выдачу без пропусков и дублей", pages == full,
              f"pages={pages} full={full}")
        check("выдача отсортирована по id", full == sorted(full), str(full))
        check("offset за пределами выдачи -> []", api_ids({"offset": 99}) == [], str(api_ids({"offset": 99})))
        check("порядок не зависит от размера страницы",
              api_ids({"limit": 1, "offset": 3}) == [full[3]], f"{api_ids({'limit': 1, 'offset': 3})} vs {full[3]}")

        show("10. 404 / 409 / 422 и сохранность данных")
        code, body, _ = call("GET", "/products/999")
        check("GET /products/999 -> 404", code == 404 and body == {"detail": "Product not found"},
              f"{code} {body}")
        code, body, _ = call("GET", "/products/0")
        check("GET /products/0 -> 404", code == 404, f"{code} {body}")
        code, body, _ = call("GET", "/products/-1")
        check("GET /products/-1 -> 404", code == 404, f"{code} {body}")
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            code, body, _ = call(method, "/products/40")
            check(f"{method} /products/40 -> 405 (маршрут записи не подключён)", code == 405, f"{code}")

        from app.services import catalog as svc

        code_before, _, raw_before = call("GET", "/products/40")
        try:
            svc.delete_product(session, 40)
        except svc.ProductDeletedConflict as exc:
            check("delete_product(40) со ссылкой из заказа -> ProductDeletedConflict", True, str(exc))
        else:
            check("delete_product(40) со ссылкой из заказа -> ProductDeletedConflict", False, "удаление прошло")
        session.rollback()
        code_after, body_after, raw_after = call("GET", "/products/40")
        check("товар 40 и три его размера целы после отказа",
              code_after == 200 and len(body_after["sizes"]) == 3, f"{code_after} {body_after}")
        check("история заказа цела",
              session.scalar(text("SELECT count(*) FROM order_items")) == 1
              and session.scalar(text("SELECT price_at_purchase FROM order_items")) == Decimal("1000.00"))
        check("сессия рабочая после отказа", session.scalar(text("SELECT count(*) FROM products")) == 7)

        # --- откат фикстура -------------------------------------------------
        session.rollback()
        session.execute(text("DELETE FROM order_items"))
        session.execute(text("DELETE FROM orders"))
        session.execute(text("DELETE FROM cart_items"))
        session.execute(text("DELETE FROM product_sizes WHERE id >= 400"))
        session.execute(text("DELETE FROM products WHERE id >= 40"))
        session.execute(text("DELETE FROM users WHERE email = 'm2-review@example.test'"))
        session.commit()
        final = {
            "brands": session.scalar(text("SELECT count(*) FROM brands")),
            "products": session.scalar(text("SELECT count(*) FROM products")),
            "product_sizes": session.scalar(text("SELECT count(*) FROM product_sizes")),
            "orders": session.scalar(text("SELECT count(*) FROM orders")),
        }
        check("фикстур убран, каталог вернулся к 2/3/4",
              final == {"brands": 2, "products": 3, "product_sizes": 4, "orders": 0}, str(final))

    ENGINE.dispose()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED ({len(failures)}): {failures}"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
