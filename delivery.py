"""
Shared "payment confirmed -> deliver coupon" logic.

Used by:
- webhook_server.py, when BharatPe's real merchant webhook confirms a payment
- bot.py's /verifypending admin command, as a manual fallback if the webhook
  is down or hasn't been set up yet

Keeping this in one place means both paths can never double-deliver a code
(mark_pending_verified in database.py only succeeds once per ref_id).
"""
import requests
import database as db
from config import BOT_TOKEN


def send_telegram_message(chat_id, text, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
    except requests.RequestException:
        # Don't let a Telegram hiccup crash the webhook handler - the order
        # is already recorded in the DB either way.
        pass


def deliver_for_ref(ref_id: str, verified_amount_cents: int, utr: str | None = None):
    """
    Confirm a pending BharatPe order as paid, claim one stock code, record the
    order, and message the buyer their code. Safe to call more than once for
    the same ref_id - only the first call does anything.

    Returns (ok: bool, detail: str) for logging / admin feedback.
    """
    pending = db.mark_pending_verified(ref_id, verified_amount_cents, utr)
    if pending is None:
        return False, f"ref_id '{ref_id}' not found or already processed"

    if verified_amount_cents < pending["amount_cents"]:
        send_telegram_message(
            pending["user_id"],
            f"⚠️ We received a payment for order `{ref_id}` but the amount "
            f"didn't match what was expected. Please contact support with "
            f"this reference so we can sort it out.",
        )
        return False, "amount mismatch"

    stock_row = db.claim_stock(pending["product_id"])
    if stock_row is None:
        send_telegram_message(
            pending["user_id"],
            f"✅ Payment for order `{ref_id}` verified, but we're temporarily "
            f"out of stock. Support will follow up with your code shortly.",
        )
        return False, "out of stock after payment - needs manual follow-up"

    db.create_order(
        user_id=pending["user_id"],
        product_id=pending["product_id"],
        stock_id=stock_row["id"],
        amount_cents=verified_amount_cents,
        telegram_charge_id=f"bharatpe:{utr or pending['utr'] or ref_id}",
    )
    db.mark_pending_delivered(ref_id)

    send_telegram_message(
        pending["user_id"],
        f"✅ Payment verified!\n\nHere is your coupon code:\n\n`{stock_row['code']}`",
    )
    return True, "delivered"
