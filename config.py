import os
from dotenv import load_dotenv

load_dotenv()

# Get this from @BotFather when you create your bot
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

# Get this from @BotFather -> Bot Settings -> Payments -> connect Stripe/Razorpay
# It looks like: "284685063:TEST:xxxxxxxxxxxx" for Stripe test mode
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "PUT_YOUR_PROVIDER_TOKEN_HERE")

# Currency code your provider supports, e.g. "USD", "INR", "EUR"
CURRENCY = os.getenv("CURRENCY", "INR")

# Comma-separated Telegram numeric user IDs allowed to run admin commands.
# Get your own ID by messaging @userinfobot
ADMIN_IDS = [
    int(uid.strip())
    for uid in os.getenv("ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
]

DB_PATH = os.getenv("DB_PATH", "coupon_bot.db")

# Channels users must join before they can use the bot (force-subscribe gate).
# Comma-separated usernames, without the @, e.g. "mychannel,mybackupchannel".
# Leave empty to disable the gate entirely.
FORCE_JOIN_CHANNELS = [
    c.strip().lstrip("@")
    for c in os.getenv("FORCE_JOIN_CHANNELS", "").split(",")
    if c.strip()
]

# Shown on the "❓ Support" menu button as a contact link.
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "your_support_username")

# --- BharatPe UPI direct flow (Option A: real merchant webhook) ---
# Your BharatPe UPI VPA, e.g. "yourshop@yesbankltd" - shown to buyers as the
# payment destination. Find this in your BharatPe merchant app/dashboard.
BHARATPE_UPI_ID = os.getenv("BHARATPE_UPI_ID", "yourshop@upi")
BHARATPE_PAYEE_NAME = os.getenv("BHARATPE_PAYEE_NAME", "Your Shop Name")

# Shared secret BharatPe (or their webhook dashboard) will send back to you
# so you can verify a webhook call actually came from them and not a spoofed
# request. Set this to whatever BharatPe's merchant webhook setup gives you.
BHARATPE_WEBHOOK_SECRET = os.getenv("BHARATPE_WEBHOOK_SECRET", "CHANGE_ME_SECRET")

# Port the local webhook receiver listens on (put this behind a real domain
# + HTTPS via nginx/caddy/ngrok before giving the URL to BharatPe).
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT") or os.getenv("PORT") or "8000")
