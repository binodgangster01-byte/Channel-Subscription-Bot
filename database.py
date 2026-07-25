"""
database.py
-----------
MongoDB-backed storage for the coupon/voucher bot.

Drop-in replacement for the SQLite version: same public function names,
arguments, and return shapes (dicts support row["field"] like sqlite3.Row).

Env vars:
    MONGODB_URI   default: mongodb://localhost:27017
    MONGODB_DB    default: coupon_shop
"""

import os
import random
import string
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING, ReturnDocument

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "coupon_shop")

_client = MongoClient(MONGODB_URI)
_db = _client[MONGODB_DB]

products_col = _db["products"]
codes_col = _db["voucher_codes"]
orders_col = _db["orders"]
counters_col = _db["counters"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _next_id(name: str) -> int:
    doc = counters_col.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def init_db():
    products_col.create_index([("id", ASCENDING)], unique=True)
    codes_col.create_index([("id", ASCENDING)], unique=True)
    codes_col.create_index([("product_id", ASCENDING), ("used", ASCENDING)])
    orders_col.create_index([("order_id", ASCENDING)], unique=True)
    orders_col.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])


# ---------------- products
def add_product(name: str, price: float, description: str = "") -> int:
    pid = _next_id("products")
    products_col.insert_one({
        "id": pid, "name": name, "price": float(price),
        "description": description, "active": 1,
    })
    return pid


def list_products(active_only: bool = True):
    q = {"active": 1} if active_only else {}
    return list(products_col.find(q, {"_id": 0}).sort("id", ASCENDING))


def get_product(product_id: int):
    return products_col.find_one({"id": int(product_id)}, {"_id": 0})


def set_product_active(product_id: int, active: bool):
    products_col.update_one(
        {"id": int(product_id)},
        {"$set": {"active": 1 if active else 0}},
    )


def stock_count(product_id: int) -> int:
    return codes_col.count_documents({"product_id": int(product_id), "used": 0})


# ---------------- voucher pool
def add_codes(product_id: int, codes: list) -> int:
    docs = []
    for c in codes:
        c = c.strip()
        if not c:
            continue
        docs.append({
            "id": _next_id("voucher_codes"),
            "product_id": int(product_id),
            "code": c, "used": 0, "order_id": None,
        })
    if docs:
        codes_col.insert_many(docs)
    return len(docs)


def _claim_codes(product_id: int, order_id: str, quantity: int):
    claimed = []
    for _ in range(quantity):
        doc = codes_col.find_one_and_update(
            {"product_id": int(product_id), "used": 0},
            {"$set": {"used": 1, "order_id": order_id}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            if claimed:
                codes_col.update_many(
                    {"id": {"$in": [c["id"] for c in claimed]}},
                    {"$set": {"used": 0, "order_id": None}},
                )
            return None
        claimed.append(doc)
    return [c["code"] for c in claimed]


# ---------------- orders
def _gen_order_id(prefix: str = "ORD") -> str:
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.hexdigits.upper()[:16], k=6))
    return f"{prefix}-{date_part}-{rand_part}"


def create_order(user_id, username, product_id, product_name,
                 unit_price, quantity=1, order_prefix="ORD") -> str:
    order_id = _gen_order_id(order_prefix)
    total = round(float(unit_price) * int(quantity), 2)
    now = _now()
    orders_col.insert_one({
        "order_id": order_id, "user_id": int(user_id), "username": username,
        "product_id": int(product_id), "product_name": product_name,
        "unit_price": float(unit_price), "quantity": int(quantity),
        "price": total, "status": "pending", "voucher_code": None,
        "created_at": now, "updated_at": now,
    })
    return order_id


def get_order(order_id: str):
    return orders_col.find_one({"order_id": order_id}, {"_id": 0})


def user_orders(user_id: int, limit: int = 10):
    return list(
        orders_col.find({"user_id": int(user_id)}, {"_id": 0})
        .sort("created_at", -1).limit(limit)
    )


def mark_paid(order_id: str):
    order = orders_col.find_one_and_update(
        {"order_id": order_id, "status": "pending"},
        {"$set": {"status": "paid_pending_delivery", "updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if order is None:
        return None
    codes = _claim_codes(order["product_id"], order_id, order["quantity"])
    if codes is None:
        orders_col.update_one(
            {"order_id": order_id},
            {"$set": {"status": "pending", "updated_at": _now()}},
        )
        return None
    orders_col.update_one(
        {"order_id": order_id},
        {"$set": {"status": "paid", "voucher_code": "\n".join(codes),
                  "updated_at": _now()}},
    )
    return codes


def expire_if_still_pending(order_id: str) -> bool:
    res = orders_col.update_one(
        {"order_id": order_id, "status": "pending"},
        {"$set": {"status": "expired", "updated_at": _now()}},
    )
    return res.modified_count == 1


def mark_rejected(order_id: str):
    orders_col.update_one(
        {"order_id": order_id},
        {"$set": {"status": "rejected", "updated_at": _now()}},
    )


def mark_cancelled(order_id: str):
    orders_col.update_one(
        {"order_id": order_id},
        {"$set": {"status": "cancelled", "updated_at": _now()}},
    )
