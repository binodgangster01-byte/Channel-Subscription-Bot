# Coupon Selling Telegram Bot

A Python Telegram bot that sells coupon codes. Users browse categories,
pick a product, pay via Telegram Payments, and get the code delivered
instantly. Includes admin commands to manage inventory.

## Setup

1. **Create the bot**
   - Message [@BotFather](https://t.me/BotFather) → `/newbot` → save the token.

2. **Connect a payment provider**
   - In BotFather: `/mybots` → your bot → *Payments* → connect Stripe,
     Razorpay, or another supported provider.
   - Copy the **provider token** it gives you (test tokens work for
     development — Telegram Stars is a token-free alternative if you'd
     rather not deal with a card processor).

3. **Get your Telegram user ID**
   - Message [@userinfobot](https://t.me/userinfobot) to get your numeric ID.
     Use this as an `ADMIN_IDS` value so you can run admin commands.

4. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure**
   ```bash
   cp .env.example .env
   # edit .env with your BOT_TOKEN, PAYMENT_PROVIDER_TOKEN, CURRENCY, ADMIN_IDS
   ```

6. **Run**
   ```bash
   python bot.py
   ```

## Admin commands (only work for IDs in `ADMIN_IDS`)

| Command | Example | Purpose |
|---|---|---|
| `/addcategory <name>` | `/addcategory Gift Cards` | Create a category |
| `/listcategories` | `/listcategories` | Show category IDs |
| `/addproduct <cat_id> <price> <name> \| <description>` | `/addproduct 1 4.99 Amazon $5 Card \| Instant delivery` | Add a product |
| `/addstock <product_id>` then one code per line | see below | Add coupon codes to inventory |
| `/stats` | `/stats` | Total orders & revenue |

Example of adding stock (send as one message):
```
/addstock 3
AMZ-CODE-0001
AMZ-CODE-0002
AMZ-CODE-0003
```

## Admin panel (`/admin`)

Beyond the text commands above, admins get a tap-through panel:

- **📢 Broadcast** — send a message to every user who's ever started the bot
- **📋 Force-Join Channels** — add/remove channels live, no redeploy needed.
  This replaces the old `.env`-only `FORCE_JOIN_CHANNELS` setting — on first
  run after upgrading, whatever was in `.env` gets copied into the database
  once, then you manage it entirely from here.
- **🎟️ Manage Coupons** — browse categories → products, tap a product to
  edit its price on the fly, or add new categories/products without typing
  the full `/addcategory` / `/addproduct` syntax
- **📊 Stats** — same numbers as `/stats`, inline

Admins are exempt from the force-join gate everywhere in the bot, so you
can always reach `/admin` even if you haven't joined the configured
channels yourself.

## Force-join gate + persistent menu

If you set `FORCE_JOIN_CHANNELS` in `.env` (comma-separated channel
usernames, no `@`), users must join every listed channel before they can
use the bot. `/start` (and any menu action) checks membership via the
Bot API and shows Join buttons + a "🔥 I've Joined — Verify" button until
they've joined all of them.

**Your bot must be an admin in each of those channels** — otherwise it
can't check who's a member and the gate fails closed (blocks everyone)
rather than silently letting people through.

Once verified, users get a persistent menu at the bottom of the chat:

- 🛍️ Buy Vouchers — browse categories/products
- 📦 My Orders — see past purchases + codes
- 🔄 Recover Vouchers — same list, for buyers who lost their code
- ❓ Support — contact link to `SUPPORT_USERNAME`

Leave `FORCE_JOIN_CHANNELS` empty to disable the gate entirely.

## User flow

- `/start` → **Browse Coupons** → pick category → pick product → **Buy Now**
- Telegram shows the native payment sheet
- On success, the bot pops one unused code from stock and sends it to the buyer
- `/orders` shows a buyer's past purchases and delivered codes

## How stock/payment safety works

- Each product has a pool of one-time-use codes in the `stock` table.
- When a buyer reaches checkout, the bot atomically claims one unsold code
  (`pre_checkout_query`) so two buyers can't be sold the same code.
- If payment actually fails after that point, you'd want to release the
  code back (`database.release_stock`) — wire that into a failed-payment
  path if your provider sends one.
- Orders are logged with the `telegram_payment_charge_id` for refund lookups.

## Deploying on Render (free tier)

Render's free plan only runs *web* services (something bound to a port for
health checks) and spins them down after 15 minutes of no traffic — a
plain long-polling bot doesn't qualify on its own. This bot ships with a
tiny built-in Flask server (`keep_alive()` in `bot.py`) purely so Render
sees it as a web service; the actual bot still runs long-polling against
Telegram in the background.

1. Push this project to a GitHub repo.
2. On Render: **New → Blueprint**, point it at the repo — `render.yaml`
   is already set up to deploy the bot correctly. (Or **New → Web Service**
   manually: build command `pip install -r requirements.txt`, start
   command `python bot.py`.)
3. Fill in the environment variables Render prompts for (`BOT_TOKEN`,
   `PAYMENT_PROVIDER_TOKEN`, `ADMIN_IDS`, etc. — see `.env.example`).
4. Once deployed, copy the service's `.onrender.com` URL.
5. **Stop it spinning down:** create a free monitor at
   [uptimerobot.com](https://uptimerobot.com) pointed at that URL, checked
   every 5 minutes (Render's free tier spin-down window is 15 min, so
   anything under that keeps it warm). The bot's `/` route just returns
   "Coupon bot is running." — that's all UptimeRobot needs to see.

**Trade-off to know:** this keeps the bot alive most of the time, but if
UptimeRobot's check ever lands right after a spin-down (rare, but
possible), the next real user message can hit a ~1 minute cold start. If
that's not acceptable for a live shop, the honest fix is Render's paid
Background Worker tier (~$7/mo) instead — no keep-alive hack needed, no
cold starts, correct service type for what this actually is. Delete the
`keep_alive()` call in `bot.py` if you switch to that.

The optional `coupon-bot-webhook` service in `render.yaml` is only needed
if you set up the real BharatPe merchant webhook (see below) — delete
that block if you're not using it.

## Notes

- Uses SQLite (`coupon_bot.db`, created automatically) — fine for
  low-to-medium volume. Swap `database.py` for Postgres/MySQL if you need
  to scale or run multiple bot instances.
- This does not use or copy any code from @CouponAdda_bot — it's a fresh
  implementation of the same general kind of bot.
