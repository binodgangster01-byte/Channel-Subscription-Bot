# Coupon/Voucher Shop Telegram Bot

A ready-to-run bot for selling coupons/vouchers, styled after the
Buy Vouchers → My Orders → Recover Vouchers → Support flow.

## What it does
- **Buy Vouchers** — shows active products with live stock. Buyer picks one,
  then picks a quantity (1 / 5 / 10 / a custom "Other amount"), then sees
  Terms & Conditions with an I Agree / Cancel step.
- **UPI QR payment page** — after I Agree, the bot creates the order and
  shows a payment page with a scannable UPI QR code, Order ID, Service, Qty,
  Amount, and a "valid for 10 minutes" countdown. Orders left unpaid after
  that window auto-expire.
- **Payment verification** — buyer taps "I've Paid"; the claim is forwarded
  to your admin group with Approve/Reject buttons (manual verification stops
  fake-payment fraud — no auto-trust of a button tap).
- **Instant delivery** — on Approve, the bot atomically pulls the right
  number of unused codes from your stock and DMs them to the buyer.
- **My Orders** — buyer sees their order history, quantity, and status.
- **Recover Vouchers** — buyer re-fetches their code(s) by Order ID.
- **Support** — buyer picks an order and messages you; you reply with
  `/reply <user_id> <message>` in the admin chat.

Order IDs are formatted like `SUMIT-20260725-0E629B` — prefixed with the
buyer's Telegram first name, then the date, then a random suffix.

## 1. Install
```bash
pip install -r requirements.txt
```

## 2. Create your bot
1. Talk to [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy the token.
2. Create a private Telegram group for yourself (this is where payment
   approvals and support messages land). Add your bot to it.
3. Get that group's chat id — easiest way: add [@userinfobot](https://t.me/userinfobot)
   to the group temporarily, or send a message in the group and check the
   bot's logs / use `getUpdates`.

## 3. Configure
Set environment variables (or edit the constants at the top of `bot.py`):

```bash
export BOT_TOKEN="123456:ABC-your-bot-token"
export ADMIN_CHAT_ID="-1001234567890"      # your admin group id
export ADMIN_USER_IDS="111111111,222222222" # your personal Telegram user id(s)
export UPI_ID="yourupi@bank"
export SHOP_NAME="My Coupon Shop"
export QR_VALID_MINUTES="10"                # optional, defaults to 10
export TERMS_TEXT="No returns after delivery. Coupons are fresh and verified — please know the usage before buying."
```

## 4. Run
```bash
python bot.py
```

## 5. Add products & stock (as admin)
In the admin chat or your DM with the bot:
```
/addproduct 150 Shein 1000 per 800 off
/products                     -> shows #id, price, live stock
/addcodes 1                   -> bot asks for codes, then paste one per line:
SHEIN-CODE-AAA111
SHEIN-CODE-BBB222
/deactivate 1                 -> hide a retired product
```

Each pasted code becomes exactly one unit of stock. When a buyer's payment
is approved, one code is claimed and can never be handed out twice.

## 6. Deploy on Render (free tier)

Render's free tier only offers **Web Services** (things that answer HTTP
requests) for free — **Background Workers cost $7/mo minimum**. This bot
uses Telegram long-polling, not HTTP, so `bot.py` includes a tiny built-in
health-check server (`start_health_server()`) purely so Render sees a live
port. This lets you run it as a free Web Service.

### ⚠️ Free tier storage warning — read this first
Render's free Web Services have **no persistent disk**: the filesystem
(including `shop.db`, i.e. all your orders and remaining stock) is wiped
every time the service restarts, redeploys, or wakes from sleep. Combined
with the 15-minute sleep timer below, this means:
- Stock you `/addcodes` today can vanish on the next spin-down/restart.
- Paid order history can disappear the same way.

**This setup is fine for testing the bot for free. It is not safe for a
real shop handling real money and real stock** until you either:
- upgrade to a paid instance ($7/mo) and attach a persistent disk, or
- point `database.py` at an external database that isn't on Render's
  ephemeral disk (e.g. a free-tier Postgres from another provider, with
  `DB_PATH`-style logic swapped for a Postgres connection).

If you just want to try it out or demo it to yourself, the free path below
works fine — just don't load real stock into it yet.

### Steps
1. Push this project to a GitHub (or GitLab) repo.
2. In Render: **New → Blueprint**, point it at your repo — it will pick up
   `render.yaml` automatically and pre-fill a free Web Service.
   (No `render.yaml`? Use **New → Web Service** instead, runtime "Python 3",
   build command `pip install -r requirements.txt`, start command
   `python bot.py`, instance type **Free**.)
3. Under **Environment**, set `BOT_TOKEN`, `ADMIN_CHAT_ID`, `ADMIN_USER_IDS`,
   and `UPI_ID` (these are marked `sync: false` in the blueprint so Render
   will prompt you for them rather than storing them in the repo).
4. Deploy. Render gives you a URL like `https://coupon-shop-bot.onrender.com`.
5. **Set up the keep-alive** (skip this and the bot goes offline after 15
   min of inactivity): create a free account at
   [UptimeRobot](https://uptimerobot.com), add an **HTTP(s)** monitor
   pointed at your Render URL, checking every 5 minutes. This keeps the
   service awake so the bot's Telegram connection stays alive.

## Notes / things you may want to change
- **Payment method**: this build uses manual UPI + admin approval, since
  that's the safest default without hooking up a real payment gateway. If
  you want automatic verification, swap in a payment gateway (Razorpay,
  Cashfree, Stripe, etc.) that gives you a webhook, and call `db.mark_paid()`
  from that webhook instead of the admin Approve button.
- **Storage**: SQLite (`shop.db`), created automatically next to `bot.py`.
  Fine for a single-instance bot; back the file up regularly.
- **Scaling admins**: add as many `ADMIN_USER_IDS` as you like, comma-separated.
- **Hosting**: any VPS, Railway, Render, or a Raspberry Pi works — just keep
  `python bot.py` running (e.g. with `systemd`, `pm2`, or `screen`).
