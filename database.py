import sqlite3
from contextlib import contextmanager
from config import DB_PATH


def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price_cents INTEGER NOT NULL,
                active INTEGER DEFAULT 1,
                FOREIGN KEY(category_id) REFERENCES categories(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                sold INTEGER DEFAULT 0,
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                stock_id INTEGER,
                amount_cents INTEGER NOT NULL,
                telegram_charge_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Orders created via the BharatPe UPI QR flow, waiting for a webhook
        # (or manual admin approval as fallback) to confirm real payment.
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'awaiting_payment',
                utr TEXT,
                verified_amount_cents INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                verified_at TEXT
            )
        """)
        # Force-join channels, managed live from the admin panel instead of
        # being fixed at deploy time via .env.
        c.execute("""
            CREATE TABLE IF NOT EXISTS force_join_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL
            )
        """)
        # Every user who has ever started the bot, so broadcasts have
        # someone to reach.
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------- Categories ----------

def add_category(name):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        conn.commit()


def list_categories():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM categories ORDER BY name").fetchall()


# ---------- Products ----------

def add_product(category_id, name, description, price_cents):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO products (category_id, name, description, price_cents) VALUES (?, ?, ?, ?)",
            (category_id, name, description, price_cents),
        )
        conn.commit()
        return cur.lastrowid


def list_products_by_category(category_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE category_id = ? AND active = 1 ORDER BY name",
            (category_id,),
        ).fetchall()


def get_product(product_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def stock_count(product_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM stock WHERE product_id = ? AND sold = 0",
            (product_id,),
        ).fetchone()
        return row["n"]


# ---------- Stock ----------

def add_stock_codes(product_id, codes):
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO stock (product_id, code) VALUES (?, ?)",
            [(product_id, code) for code in codes],
        )
        conn.commit()


def claim_stock(product_id):
    """Atomically claim one unsold code for a product. Returns the row or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM stock WHERE product_id = ? AND sold = 0 LIMIT 1",
            (product_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE stock SET sold = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return row


def release_stock(stock_id):
    """Put a claimed code back if payment fails after claiming."""
    with get_conn() as conn:
        conn.execute("UPDATE stock SET sold = 0 WHERE id = ?", (stock_id,))
        conn.commit()


# ---------- Orders ----------

def create_order(user_id, product_id, stock_id, amount_cents, telegram_charge_id):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders (user_id, product_id, stock_id, amount_cents, telegram_charge_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, product_id, stock_id, amount_cents, telegram_charge_id),
        )
        conn.commit()
        return cur.lastrowid


def user_orders(user_id):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT orders.*, products.name AS product_name, stock.code AS code
            FROM orders
            JOIN products ON products.id = orders.product_id
            LEFT JOIN stock ON stock.id = orders.stock_id
            WHERE orders.user_id = ?
            ORDER BY orders.created_at DESC
            """,
            (user_id,),
        ).fetchall()


# ---------- Pending BharatPe orders (QR + webhook flow) ----------

def create_pending_order(ref_id, user_id, product_id, amount_cents):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pending_orders (ref_id, user_id, product_id, amount_cents) "
            "VALUES (?, ?, ?, ?)",
            (ref_id, user_id, product_id, amount_cents),
        )
        conn.commit()


def get_pending_order(ref_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_orders WHERE ref_id = ?", (ref_id,)
        ).fetchone()


def find_pending_by_utr(utr):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_orders WHERE utr = ? AND status = 'awaiting_payment'",
            (utr,),
        ).fetchone()


def attach_utr(ref_id, utr):
    """Buyer tells us which UTR they paid with, before the webhook confirms it."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_orders SET utr = ? WHERE ref_id = ? AND status = 'awaiting_payment'",
            (utr, ref_id),
        )
        conn.commit()


def mark_pending_verified(ref_id, verified_amount_cents, utr=None):
    """Webhook (or admin) confirms real payment happened. Returns the row, or
    None if it was already verified/doesn't exist (prevents double-delivery)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_orders WHERE ref_id = ? AND status = 'awaiting_payment'",
            (ref_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE pending_orders SET status = 'verified', verified_amount_cents = ?, "
            "utr = COALESCE(?, utr), verified_at = CURRENT_TIMESTAMP WHERE ref_id = ?",
            (verified_amount_cents, utr, ref_id),
        )
        conn.commit()
        return row


def mark_pending_delivered(ref_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_orders SET status = 'delivered' WHERE ref_id = ?", (ref_id,)
        )
        conn.commit()


def sales_stats():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n_orders, COALESCE(SUM(amount_cents), 0) AS total_cents FROM orders"
        ).fetchone()
        return row


# ---------- Force-join channels (admin-managed) ----------

def list_force_channels():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM force_join_channels ORDER BY username").fetchall()


def add_force_channel(username):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO force_join_channels (username) VALUES (?)", (username,))
        conn.commit()


def remove_force_channel(username):
    with get_conn() as conn:
        conn.execute("DELETE FROM force_join_channels WHERE username = ?", (username,))
        conn.commit()


# ---------- Product price editing ----------

def update_product_price(product_id, price_cents):
    with get_conn() as conn:
        conn.execute("UPDATE products SET price_cents = ? WHERE id = ?", (price_cents, product_id))
        conn.commit()


# ---------- Users (for broadcast) ----------

def upsert_user(user_id):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()


def list_all_user_ids():
    with get_conn() as conn:
        return [row["user_id"] for row in conn.execute("SELECT user_id FROM users").fetchall()]
