import io
import logging
import os
import uuid
from threading import Thread
from urllib.parse import quote

import qrcode
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import database as db
from delivery import deliver_for_ref
from config import (
    BOT_TOKEN,
    PAYMENT_PROVIDER_TOKEN,
    CURRENCY,
    ADMIN_IDS,
    BHARATPE_UPI_ID,
    BHARATPE_PAYEE_NAME,
    FORCE_JOIN_CHANNELS,
    SUPPORT_USERNAME,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keep-alive web server (for Render's free Web Service tier)
#
# Render's free plan only runs *web* services - it needs something bound to
# a port to health-check. This tiny Flask app does nothing but respond
# "OK", running in a background thread while the actual bot polls Telegram
# normally. Point UptimeRobot (or any uptime pinger) at this bot's Render
# URL every 5-10 minutes to stop it spinning down after 15 min idle.
#
# If you're deploying as a paid Background Worker instead (no cold starts,
# no pinger needed), you can safely delete this section and just call
# main() directly at the bottom of the file.
# ---------------------------------------------------------------------------

keep_alive_app = Flask('')


@keep_alive_app.route('/')
def _keep_alive_home():
    return "Coupon bot is running."


def _run_keep_alive_web():
    port = int(os.environ.get("PORT", 5000))
    keep_alive_app.run(host='0.0.0.0', port=port)


def keep_alive():
    Thread(target=_run_keep_alive_web, daemon=True).start()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ---------------------------------------------------------------------------
# User-facing flow
# ---------------------------------------------------------------------------

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🛍️ Buy Vouchers", "📦 My Orders"],
            ["🔄 Recover Vouchers", "❓ Support"],
        ],
        resize_keyboard=True,
    )


async def send_main_menu(chat_id, context, text="Welcome! Use the menu below to get started."):
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=main_menu_keyboard())


async def is_member_of_all(context, user_id) -> bool:
    """Checks the user has joined every channel currently configured via
    the admin panel. If membership can't be checked (e.g. bot isn't admin
    in that channel), fails closed - treats it as not-joined rather than
    silently letting everyone through."""
    channels = [c["username"] for c in db.list_force_channels()]
    for channel in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            logger.warning(f"Could not check membership for @{channel}: {e}")
            return False
    return True


async def send_join_gate(chat_id, context):
    channels = [c["username"] for c in db.list_force_channels()]
    keyboard = [
        [InlineKeyboardButton(f"📍 Join @{channel}", url=f"https://t.me/{channel}")]
        for channel in channels
    ]
    keyboard.append([InlineKeyboardButton("🔥 I've Joined — Verify", callback_data="verify_join")])
    await context.bot.send_message(
        chat_id=chat_id,
        text="Join the channel(s) below to continue.\n\nAfter joining, tap 🔥 Verify below.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the user may proceed. Otherwise sends the join gate
    and returns False so the caller can stop handling the update."""
    db.upsert_user(update.effective_user.id)
    if is_admin(update.effective_user.id):
        return True
    if not db.list_force_channels():
        return True
    if await is_member_of_all(context, update.effective_user.id):
        return True
    await send_join_gate(update.effective_chat.id, context)
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update, context):
        return
    await send_main_menu(update.effective_chat.id, context)


async def send_categories_message(chat_id, context):
    categories = db.list_categories()
    if not categories:
        await context.bot.send_message(chat_id, "No categories available yet. Check back soon!")
        return
    keyboard = [
        [InlineKeyboardButton(c["name"], callback_data=f"cat_{c['id']}")] for c in categories
    ]
    await context.bot.send_message(chat_id, "Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard))


async def send_orders_message(update: Update, context: ContextTypes.DEFAULT_TYPE, header: str):
    orders = db.user_orders(update.effective_user.id)
    if not orders:
        await update.message.reply_text("You haven't bought anything yet.")
        return
    lines = [f"*{header}*"]
    for o in orders:
        lines.append(f"• {o['product_name']} — {o['amount_cents']/100:.2f} {CURRENCY} — code: `{o['code']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def send_support_message(chat_id, context):
    keyboard = [[InlineKeyboardButton("📞 Contact Support", url=f"https://t.me/{SUPPORT_USERNAME}")]]
    await context.bot.send_message(
        chat_id, "Need help? Tap below to message support.", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update, context):
        return
    text = update.message.text
    if text == "🛍️ Buy Vouchers":
        await send_categories_message(update.effective_chat.id, context)
    elif text == "📦 My Orders":
        await send_orders_message(update, context, header="📦 Your Orders")
    elif text == "🔄 Recover Vouchers":
        await send_orders_message(update, context, header="🔄 Recovered Vouchers")
    elif text == "❓ Support":
        await send_support_message(update.effective_chat.id, context)


async def help_text(update_or_query, edit=False):
    text = (
        "*How this bot works*\n"
        "1. Tap Browse Coupons and pick a category\n"
        "2. Pick a product and tap Buy\n"
        "3. Pay securely via Telegram Payments\n"
        "4. Your coupon code is delivered instantly\n\n"
        "Use /orders to see your past purchases."
    )
    if edit:
        await update_or_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update_or_query.message.reply_text(text, parse_mode="Markdown")


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "verify_join":
        if await is_member_of_all(context, query.from_user.id):
            await query.answer()
            await query.edit_message_text("🔥 Verified!\n\nWelcome!")
            await send_main_menu(query.message.chat_id, context)
        else:
            await query.answer("Please join all the channels first, then tap Verify again.", show_alert=True)
        return

    if is_admin(query.from_user.id):
        pass
    elif db.list_force_channels() and not await is_member_of_all(context, query.from_user.id):
        await query.answer("Please join our channel(s) first. Send /start to see the join links.", show_alert=True)
        return

    await query.answer()

    if data == "browse":
        await show_categories(query)
    elif data == "orders":
        await show_orders(query)
    elif data == "help":
        await help_text(query, edit=True)
    elif data == "back_main":
        keyboard = [
            [InlineKeyboardButton("🛍️ Browse Coupons", callback_data="browse")],
            [InlineKeyboardButton("📦 My Orders", callback_data="orders")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
        await query.edit_message_text(
            "Welcome! Browse and buy coupon codes below.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif data.startswith("cat_"):
        category_id = int(data.split("_", 1)[1])
        await show_products(query, category_id)
    elif data.startswith("prod_"):
        product_id = int(data.split("_", 1)[1])
        await show_product_detail(query, product_id)
    elif data.startswith("buy_"):
        product_id = int(data.split("_", 1)[1])
        await send_product_invoice(update, context, product_id)
    elif data.startswith("bp_"):
        product_id = int(data.split("_", 1)[1])
        await send_bharatpe_qr(update, context, product_id)
    elif data == "adm_panel":
        if is_admin(query.from_user.id):
            await show_admin_panel(query)
    elif data == "adm_broadcast":
        if is_admin(query.from_user.id):
            context.user_data["awaiting"] = "broadcast"
            await query.edit_message_text("📢 Send the message to broadcast to all users.\n\nSend /canceladmin to abort.")
    elif data == "adm_stats":
        if is_admin(query.from_user.id):
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="adm_panel")]]
            await query.edit_message_text(format_stats_text(), reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "adm_channels":
        if is_admin(query.from_user.id):
            await show_force_channels_admin(query)
    elif data == "adm_add_channel":
        if is_admin(query.from_user.id):
            context.user_data["awaiting"] = "add_channel"
            await query.edit_message_text("Send the channel username to add (without @).\n\nSend /canceladmin to abort.")
    elif data.startswith("adm_rmch_"):
        if is_admin(query.from_user.id):
            username = data.split("_", 2)[2]
            db.remove_force_channel(username)
            await show_force_channels_admin(query, notice=f"Removed @{username}.")
    elif data == "adm_coupons":
        if is_admin(query.from_user.id):
            await show_coupon_categories_admin(query)
    elif data == "adm_add_category":
        if is_admin(query.from_user.id):
            context.user_data["awaiting"] = "add_category"
            await query.edit_message_text("Send the new category name.\n\nSend /canceladmin to abort.")
    elif data.startswith("admcat_"):
        if is_admin(query.from_user.id):
            category_id = int(data.split("_", 1)[1])
            await show_coupon_products_admin(query, category_id)
    elif data.startswith("admeditprice_"):
        if is_admin(query.from_user.id):
            product_id = int(data.split("_", 1)[1])
            context.user_data["awaiting"] = "edit_price"
            context.user_data["edit_price_product_id"] = product_id
            await query.edit_message_text("Send the new price (e.g. 4.99).\n\nSend /canceladmin to abort.")
    elif data.startswith("admaddprod_"):
        if is_admin(query.from_user.id):
            category_id = int(data.split("_", 1)[1])
            context.user_data["awaiting"] = "add_product"
            context.user_data["add_product_category_id"] = category_id
            await query.edit_message_text(
                "Send: price name | description\n"
                "Example: 4.99 Amazon $5 Card | Instant delivery\n\n"
                "Send /canceladmin to abort."
            )


async def show_categories(query):
    categories = db.list_categories()
    if not categories:
        await query.edit_message_text("No categories available yet. Check back soon!")
        return
    keyboard = [
        [InlineKeyboardButton(c["name"], callback_data=f"cat_{c['id']}")] for c in categories
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
    await query.edit_message_text("Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_products(query, category_id):
    products = db.list_products_by_category(category_id)
    if not products:
        await query.edit_message_text(
            "No products in this category yet.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="browse")]]
            ),
        )
        return
    keyboard = []
    for p in products:
        left = db.stock_count(p["id"])
        label = f"{p['name']} - {p['price_cents']/100:.2f} {CURRENCY} ({left} left)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"prod_{p['id']}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="browse")])
    await query.edit_message_text("Choose a product:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_product_detail(query, product_id):
    p = db.get_product(product_id)
    if p is None:
        await query.edit_message_text("Product not found.")
        return
    left = db.stock_count(product_id)
    text = (
        f"*{p['name']}*\n"
        f"{p['description']}\n\n"
        f"Price: {p['price_cents']/100:.2f} {CURRENCY}\n"
        f"In stock: {left}"
    )
    buttons = []
    if left > 0:
        buttons.append([InlineKeyboardButton("💳 Buy with card/UPI (instant)", callback_data=f"buy_{product_id}")])
        buttons.append([InlineKeyboardButton("📲 Pay via BharatPe UPI", callback_data=f"bp_{product_id}")])
    else:
        buttons.append([InlineKeyboardButton("Out of stock", callback_data="noop")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"cat_{p['category_id']}")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def send_product_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    p = db.get_product(product_id)
    query = update.callback_query
    if p is None:
        await query.answer("Product not found.", show_alert=True)
        return
    if db.stock_count(product_id) <= 0:
        await query.answer("Sorry, just sold out.", show_alert=True)
        return

    chat_id = query.message.chat_id
    payload = f"product_{product_id}"
    prices = [LabeledPrice(p["name"], p["price_cents"])]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=p["name"],
        description=p["description"] or p["name"],
        payload=payload,
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=prices,
        start_parameter=f"buy-{product_id}",
    )


async def send_bharatpe_qr(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    p = db.get_product(product_id)
    query = update.callback_query
    if p is None:
        await query.answer("Product not found.", show_alert=True)
        return
    if db.stock_count(product_id) <= 0:
        await query.answer("Sorry, just sold out.", show_alert=True)
        return

    # Short, random reference so we can match this specific order later.
    # We put it in the UPI "tn" (transaction note) field - whether BharatPe
    # passes that through in their webhook payload depends on your merchant
    # setup, so treat the ref_id shown to the buyer as the source of truth
    # and cross-check it manually if the webhook can't parse it out.
    ref_id = uuid.uuid4().hex[:10]
    amount_rupees = p["price_cents"] / 100
    db.create_pending_order(ref_id, query.from_user.id, product_id, p["price_cents"])

    upi_link = (
        f"upi://pay?pa={quote(BHARATPE_UPI_ID)}"
        f"&pn={quote(BHARATPE_PAYEE_NAME)}"
        f"&am={amount_rupees:.2f}"
        f"&tn={quote(ref_id)}"
        f"&cu=INR"
    )

    qr_img = qrcode.make(upi_link)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)

    caption = (
        f"*{p['name']}*\n"
        f"Amount: {amount_rupees:.2f} INR\n"
        f"UPI ID: `{BHARATPE_UPI_ID}`\n"
        f"Order ref: `{ref_id}`\n\n"
        f"Scan this QR with any UPI app (or BharatPe) to pay the exact amount.\n"
        f"Please keep the reference `{ref_id}` in the payment note if your "
        f"app allows it - it helps us match your payment automatically.\n\n"
        f"Once paid, reply here with:\n"
        f"`/paid {ref_id} <your UTR number>`\n\n"
        f"We'll confirm and deliver your code as soon as the payment is verified."
    )
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=buf,
        caption=caption,
        parse_mode="Markdown",
    )


async def paid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buyer tells us their UTR after paying via the BharatPe QR flow.
    This just records the UTR for matching/admin reference - it does NOT
    verify or deliver anything by itself. Delivery only happens once the
    real BharatPe webhook (or an admin via /verifypending) confirms payment."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /paid <ref_id> <utr>")
        return
    ref_id, utr = context.args[0], context.args[1]
    pending = db.get_pending_order(ref_id)
    if pending is None or pending["user_id"] != update.effective_user.id:
        await update.message.reply_text("I couldn't find that order under your account. Double-check the ref_id.")
        return
    if pending["status"] != "awaiting_payment":
        await update.message.reply_text("That order has already been processed.")
        return
    db.attach_utr(ref_id, utr)
    await update.message.reply_text(
        "Got it - we'll auto-verify this against your payment shortly. "
        "You'll get a message here the moment it's confirmed."
    )


def build_admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
        [InlineKeyboardButton("📋 Force-Join Channels", callback_data="adm_channels")],
        [InlineKeyboardButton("🎟️ Manage Coupons", callback_data="adm_coupons")],
        [InlineKeyboardButton("📊 Stats", callback_data="adm_stats")],
    ])


async def show_admin_panel(query_or_message, is_query=True):
    text = "🛠 *Admin Panel*\n\nChoose an option:"
    keyboard = build_admin_panel_keyboard()
    if is_query:
        await query_or_message.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await query_or_message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


def format_stats_text():
    row = db.sales_stats()
    return f"Total orders: {row['n_orders']}\nTotal revenue: {row['total_cents']/100:.2f} {CURRENCY}"


async def show_force_channels_admin(query, notice=None):
    channels = db.list_force_channels()
    lines = ["📋 *Force-Join Channels*"]
    if not channels:
        lines.append("(none set — join gate is currently disabled)")
    else:
        lines.append(f"Users must join all {len(channels)} channel(s) below to use the bot.")
    if notice:
        lines.append(f"\n{notice}")

    keyboard = [
        [InlineKeyboardButton(f"❌ Remove @{c['username']}", callback_data=f"adm_rmch_{c['username']}")]
        for c in channels
    ]
    keyboard.append([InlineKeyboardButton("➕ Add Channel", callback_data="adm_add_channel")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_panel")])
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_coupon_categories_admin(query):
    categories = db.list_categories()
    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"admcat_{c['id']}")] for c in categories]
    keyboard.append([InlineKeyboardButton("➕ Add Category", callback_data="adm_add_category")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_panel")])
    text = "🎟️ Choose a category to manage:" if categories else "🎟️ No categories yet — add one below."
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_coupon_products_admin(query, category_id):
    products = db.list_products_by_category(category_id)
    keyboard = []
    for p in products:
        left = db.stock_count(p["id"])
        keyboard.append([InlineKeyboardButton(
            f"✏️ {p['name']} — {p['price_cents']/100:.2f} {CURRENCY} ({left} left)",
            callback_data=f"admeditprice_{p['id']}",
        )])
    keyboard.append([InlineKeyboardButton("➕ Add Product Here", callback_data=f"admaddprod_{category_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_coupons")])
    text = "🎟️ Tap a product to edit its price:" if products else "🎟️ No products in this category yet."
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You're not authorized to use this command.")
        return
    await show_admin_panel(update.message, is_query=False)


async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles free-text replies while an admin is mid-flow in the panel
    (broadcast message, new channel username, new price, etc). Does nothing
    - and lets other handlers process the update normally - unless the
    sender is an admin with a pending 'awaiting' state."""
    if not is_admin(update.effective_user.id):
        return
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return
    text = (update.message.text or "").strip()

    if text == "/canceladmin":
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("Cancelled.")
        return

    if awaiting == "broadcast":
        user_ids = db.list_all_user_ids()
        sent, failed = 0, 0
        for uid in user_ids:
            try:
                await context.bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(f"📢 Broadcast complete.\n\nSent: {sent}\nFailed (blocked/inactive): {failed}")
        context.user_data.pop("awaiting", None)

    elif awaiting == "add_channel":
        username = text.lstrip("@").strip()
        db.add_force_channel(username)
        await update.message.reply_text(
            f"✅ Added @{username} to the force-join list.\n\n"
            f"⚠️ Make sure your bot is an admin in that channel, or membership "
            f"checks will fail and block everyone."
        )
        context.user_data.pop("awaiting", None)

    elif awaiting == "add_category":
        db.add_category(text)
        await update.message.reply_text(f"✅ Category '{text}' added.")
        context.user_data.pop("awaiting", None)

    elif awaiting == "edit_price":
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("Please send a number, e.g. 4.99")
            return
        product_id = context.user_data.get("edit_price_product_id")
        db.update_product_price(product_id, int(round(price * 100)))
        await update.message.reply_text(f"✅ Price updated to {price:.2f} {CURRENCY}.")
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit_price_product_id", None)

    elif awaiting == "add_product":
        category_id = context.user_data.get("add_product_category_id")
        try:
            parts = text.split(" ", 1)
            price = float(parts[0])
            rest = parts[1]
            if "|" in rest:
                name, description = rest.split("|", 1)
            else:
                name, description = rest, ""
            name, description = name.strip(), description.strip()
        except (IndexError, ValueError):
            await update.message.reply_text(
                "Format: price name | description\nExample: 4.99 Amazon $5 Card | Instant delivery"
            )
            return
        pid = db.add_product(category_id, name, description, int(round(price * 100)))
        await update.message.reply_text(f"✅ Product '{name}' added (id {pid}).")
        context.user_data.pop("awaiting", None)
        context.user_data.pop("add_product_category_id", None)


async def show_orders(query):
    orders = db.user_orders(query.from_user.id)
    if not orders:
        await query.edit_message_text(
            "You haven't bought anything yet.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_main")]]
            ),
        )
        return
    lines = ["*Your orders:*"]
    for o in orders:
        lines.append(
            f"• {o['product_name']} — {o['amount_cents']/100:.2f} {CURRENCY} — code: `{o['code']}`"
        )
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="back_main")]]
        ),
    )


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = db.user_orders(update.effective_user.id)
    if not orders:
        await update.message.reply_text("You haven't bought anything yet.")
        return
    lines = ["Your orders:"]
    for o in orders:
        lines.append(f"• {o['product_name']} — {o['amount_cents']/100:.2f} {CURRENCY} — code: {o['code']}")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Payment flow
# ---------------------------------------------------------------------------

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    payload = query.invoice_payload
    if not payload.startswith("product_"):
        await query.answer(ok=False, error_message="Invalid order, please try again.")
        return

    product_id = int(payload.split("_", 1)[1])
    stock_row = db.claim_stock(product_id)
    if stock_row is None:
        await query.answer(ok=False, error_message="Sorry, this item just sold out.")
        return

    # Remember which stock item this specific checkout claimed so we can
    # deliver the same code (and release it if payment ends up failing).
    context.bot_data.setdefault("pending_claims", {})[
        (query.from_user.id, payload)
    ] = stock_row["id"]

    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    user_id = update.effective_user.id
    product_id = int(payload.split("_", 1)[1])

    stock_id = context.bot_data.get("pending_claims", {}).pop((user_id, payload), None)
    if stock_id is None:
        # Fallback: claim now if for some reason we lost track of it
        row = db.claim_stock(product_id)
        stock_id = row["id"] if row else None

    db.create_order(
        user_id=user_id,
        product_id=product_id,
        stock_id=stock_id,
        amount_cents=payment.total_amount,
        telegram_charge_id=payment.telegram_payment_charge_id,
    )

    if stock_id is not None:
        with db.get_conn() as conn:
            code = conn.execute("SELECT code FROM stock WHERE id = ?", (stock_id,)).fetchone()["code"]
        await update.message.reply_text(
            f"✅ Payment received! Here is your coupon code:\n\n`{code}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "✅ Payment received, but we couldn't find a code to deliver. "
            "Please contact support with your payment ID: "
            f"{payment.telegram_payment_charge_id}"
        )


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def admin_only(update: Update) -> bool:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You're not authorized to use this command.")
        return False
    return True


async def add_category_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addcategory <name>")
        return
    name = " ".join(context.args)
    db.add_category(name)
    await update.message.reply_text(f"Category '{name}' added.")


async def list_categories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    cats = db.list_categories()
    if not cats:
        await update.message.reply_text("No categories yet.")
        return
    await update.message.reply_text(
        "\n".join(f"{c['id']}: {c['name']}" for c in cats)
    )


async def add_product_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    # Usage: /addproduct <category_id> <price> <name> | <description>
    text = update.message.text.partition(" ")[2]
    try:
        parts = text.split(" ", 2)
        category_id = int(parts[0])
        price = float(parts[1])
        rest = parts[2]
        if "|" in rest:
            name, description = rest.split("|", 1)
        else:
            name, description = rest, ""
        name = name.strip()
        description = description.strip()
        price_cents = int(round(price * 100))
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: /addproduct <category_id> <price> <name> | <description>\n"
            "Example: /addproduct 1 4.99 Amazon $5 Gift Card | Instant delivery"
        )
        return

    product_id = db.add_product(category_id, name, description, price_cents)
    await update.message.reply_text(f"Product '{name}' added with id {product_id}.")


async def add_stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    # Usage: /addstock <product_id>
    # CODE1
    # CODE2
    # CODE3
    text = update.message.text
    lines = text.split("\n")
    first_line = lines[0]
    args = first_line.split(" ")
    if len(args) < 2 or not args[1].isdigit():
        await update.message.reply_text(
            "Usage:\n/addstock <product_id>\nCODE1\nCODE2\nCODE3"
        )
        return
    product_id = int(args[1])
    codes = [line.strip() for line in lines[1:] if line.strip()]
    if not codes:
        await update.message.reply_text("No codes found. Put one code per line after the command.")
        return
    db.add_stock_codes(product_id, codes)
    await update.message.reply_text(f"Added {len(codes)} code(s) to product {product_id}.")


async def verify_pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual fallback for admins: /verifypending <ref_id> <amount> [utr]
    Use this if the real BharatPe webhook isn't set up yet, or is briefly
    down, and you've confirmed the payment yourself in your BharatPe app."""
    if not await admin_only(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /verifypending <ref_id> <amount> [utr]")
        return
    ref_id = context.args[0]
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number, e.g. 4.99")
        return
    utr = context.args[2] if len(context.args) > 2 else None
    amount_cents = int(round(amount * 100))

    ok, detail = deliver_for_ref(ref_id, amount_cents, utr)
    await update.message.reply_text(f"{'✅' if ok else '⚠️'} {detail}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    await update.message.reply_text(format_stats_text())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    db.init_db()

    # One-time seed: if FORCE_JOIN_CHANNELS was set in .env and the DB table
    # is still empty, carry those channels over so upgrading doesn't wipe
    # an existing setup. After this, manage channels via /admin instead.
    if FORCE_JOIN_CHANNELS and not db.list_force_channels():
        for channel in FORCE_JOIN_CHANNELS:
            db.add_force_channel(channel)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("paid", paid_command))
    app.add_handler(CommandHandler("addcategory", add_category_cmd))
    app.add_handler(CommandHandler("listcategories", list_categories_cmd))
    app.add_handler(CommandHandler("addproduct", add_product_cmd))
    app.add_handler(CommandHandler("addstock", add_stock_cmd))
    app.add_handler(CommandHandler("verifypending", verify_pending_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("admin", admin_panel_cmd))

    app.add_handler(CallbackQueryHandler(button_router))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(
        filters.Regex("^(🛍️ Buy Vouchers|📦 My Orders|🔄 Recover Vouchers|❓ Support)$"),
        menu_text_handler,
    ))
    # Runs in its own group so it can look at admin free-text replies
    # without blocking the regular handlers above from also processing
    # the same update when there's nothing pending.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler), group=-1)

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    keep_alive()
    main()
