import sqlite3
import json
import re
import requests
from typing import Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.error import Forbidden, NetworkError

# ===================== CONFIG =====================
BOT_TOKEN = "7633684538:AAHNz4rr3FFskWSuVBaEaE45Ts-54zltw0g"
CHANNEL_ID = "@taskblixosint"

API_KEY = "7658050410:qQ88TxXl"
# If your docs use a slightly different path, we handle both:
API_URLS = [
    "https://leakosintapi.com/",        # primary as per your earlier code/docs
    "https://leakosintapi.com/api",     # fallback path
]

# Admins who can give credits
ADMINS = [7658050410]  # <- Your Telegram user ID (not a bot token)

# Shorter HTTP timeouts to reduce perceived lag
HTTP_TIMEOUT = (6, 20)  # (connect, read)

# ===================== DB SETUP =====================
conn = sqlite3.connect("bot.db", check_same_thread=False)
c = conn.cursor()
c.execute(
    """CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        credits INTEGER DEFAULT 0
    )"""
)
conn.commit()

# ===================== HELPERS =====================
MAIN_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔍 Search", callback_data="menu_search")],
    [
        InlineKeyboardButton("📘 User Manual", callback_data="menu_manual"),
        InlineKeyboardButton("⚠️ Disclaimer", callback_data="menu_disclaimer"),
    ],
    [
        InlineKeyboardButton("👤 Owner", url="https://t.me/taskblix"),
        InlineKeyboardButton("💳 Credits", callback_data="menu_credits"),
    ],
])

SEARCH_MENU_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📱 Number", callback_data="search_number"),
        InlineKeyboardButton("✉️ Email", callback_data="search_email"),
        InlineKeyboardButton("🧑 Name", callback_data="search_name"),
    ],
    [InlineKeyboardButton("⬅️ Back", callback_data="back_main")],
])

DISCLAIMER_TEXT = (
    "This bot is for educational & lawful OSINT purposes only. "
    "Do not use it to harass, stalk, defraud, or violate privacy or laws. "
    "You are solely responsible for how you use results. The bot/owner assumes no liability."
)

MANUAL_TEXT = (
    "How to use:\n"
    "• Number search: use `/num +919876543210` (must include +91)\n"
    "• Email search: `/email someone@example.com`\n"
    "• Name search: `/name First Last`\n"
    "• Check credits: `/credits`\n"
    "• Only admin can add credits: `/addcredit <userid> <amount>`\n\n"
    "Tips:\n"
    "• For numbers, ALWAYS include +91 (e.g., `/num +919123456789`).\n"
    "• Results depend on external sources. If you see an error, try again later."
)

ASYNC_ERROR_POPUP = (
    "SOMETHING WENT WRONG\n\n"
    "PLEASE CONTACT - @TASKBLIX\n"
    "TO GET DETAILS"
)

def add_user(user_id: int, username: str) -> bool:
    c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute(
            "INSERT INTO users (user_id, username, credits) VALUES (?, ?, ?)",
            (user_id, username, 15),
        )
        conn.commit()
        return True
    return False

async def safe_send(update: Update, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    try:
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
        elif update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e:
        print("Send error:", e)

def format_entry(entry: Dict[str, Any]) -> str:
    """Pretty-print one entry, each field on its own line, hide noisy keys like InfoLeak."""
    lines = []
    f = entry.get

    # Common identity fields
    if f("FullName"):   lines.append(f"👤 Name: {f('FullName')}")
    if f("NickName"):   lines.append(f"📝 Nickname: {f('NickName')}")
    if f("FatherName"): lines.append(f"👨‍👦 Father: {f('FatherName')}")
    if f("DocNumber"):  lines.append(f"🆔 DocNumber: {f('DocNumber')}")

    # Addresses (Address, Address2, Address3)
    for i in range(1, 4):
        key = "Address" if i == 1 else f"Address{i}"
        if f(key):
            lines.append(f"🏠 {key}: {f(key)}")

    # Phones (Phone..Phone9)
    for i in range(1, 10):
        key = "Phone" if i == 1 else f"Phone{i}"
        if f(key):
            lines.append(f"📞 {key}: {f(key)}")

    # Region/email/etc (optional)
    if f("Region"): lines.append(f"🌐 Region: {f('Region')}")
    if f("Email"):  lines.append(f"✉️ Email: {f('Email')}")

    return "\n".join(lines)

def format_api_response(data: Dict[str, Any], query: str) -> str:
    # Known error shapes from your API
    if isinstance(data, dict):
        if str(data.get("Status", "")).lower() == "error":
            return f"⚠️ Error from API: {data.get('Error code') or data.get('message') or 'Unknown error'}"
        if "error" in data and data["error"]:
            return f"⚠️ Error from API: {data['error']}"

    out = [f"🔍 Lookup result for {query}:\n"]
    found = False

    if isinstance(data, dict) and "List" in data and isinstance(data["List"], dict):
        for source, source_data in data["List"].items():
            # Skip verbose noise like InfoLeak text if present
            entries = source_data.get("Data") or source_data.get("data") or []
            if not entries:
                continue
            found = True
            out.append(f"📂 Source: {source}")
            for entry in entries:
                entry_txt = format_entry(entry)
                if entry_txt.strip():
                    out.append(entry_txt)
                out.append("")  # blank line between records

    if not found:
        # Fallback: compact pretty print
        try:
            out.append("```")
            out.append(json.dumps(data, indent=2, ensure_ascii=False))
            out.append("```")
        except Exception:
            out.append(str(data))

    return "\n".join(out).strip()

def valid_india_number(s: str) -> bool:
    # Must start with +91 and then 10 digits
    return bool(re.fullmatch(r"\+91\d{10}", s.strip()))

def api_call(query: str) -> Dict[str, Any] | str:
    """Try primary then fallback endpoints; return dict on JSON success or string on hard error."""
    payload = {
        "token": API_KEY,
        "request": query,
        "limit": 100,
        "lang": "en",
    }
    headers = {"Content-Type": "application/json"}
    last_err = None
    for url in API_URLS:
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
            # Try to parse JSON always; API sometimes returns {'error':..., 'Status':...}
            data = r.json()
            return data
        except Exception as e:
            last_err = e
            continue
    return f"{last_err}"

async def require_channel_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if user is ALLOWED (already a member), else sends join button and returns False."""
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ("member", "administrator", "creator"):
            return True
        # Not a member -> show join button
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Join Channel", url=f"https://t.me/{CHANNEL_ID[1:]}")]])
        await safe_send(update, "📢 Please join our channel first!", reply_markup=kb)
        return False
    except Forbidden:
        # If we can't check (e.g. channel privacy), we let them pass to avoid hard blocks
        return True
    except Exception:
        # Fallback: allow rather than block due to temporary errors
        return True

# ===================== COMMANDS =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    new_user = add_user(user_id, username)

    if new_user:
        await safe_send(update, f"✅ Welcome {username}! You got 15 free credits 🎁", reply_markup=MAIN_MENU_KB)
    else:
        c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
        credits = c.fetchone()[0]
        await safe_send(update, f"✅ Welcome back {username}! You have {credits} credits.", reply_markup=MAIN_MENU_KB)

    await require_channel_join(update, context)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_send(update, "📍 Main Menu", reply_markup=MAIN_MENU_KB)

async def credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    if result:
        await safe_send(update, f"💳 You have {result[0]} credits.")
    else:
        await safe_send(update, "⚠️ Please use /start first.")

# --------- LOOKUPS (deduct only on success) ----------
async def do_lookup(update: Update, query: str, require_plus91: bool = False):
    user_id = update.effective_user.id

    # Start-gate
    c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        await safe_send(update, "⚠️ Please use /start first.")
        return
    credits = row[0]
    if credits < 1:
        await safe_send(update, "❌ No credits left! Ask admin for credits.")
        return

    # Channel join gate
    if not await require_channel_join(update, context=None if isinstance(update, Update) else None):
        # This branch not actually used; kept for safety
        pass

    # Validation
    if require_plus91 and not valid_india_number(query):
        await safe_send(update,
            "❌ Please enter the number with country code.\n"
            "Example: `/num +919876543210`",
        )
        return

    # Call API (no credit deducted yet)
    try:
        data = api_call(query)
        if isinstance(data, str):
            # hard error
            await safe_send(update, f"⚠️ Error: {data}")
            return

        # Check obvious token errors WITHOUT deducting credits
        err_text = (data.get("Error code") or data.get("error") or "").lower()
        if "invalid token" in err_text or "bad token" in err_text:
            await safe_send(update, "⚠️ API token error. Please contact @taskblix.")
            return

        # Consider success if we have List with some data OR explicit OK
        success = False
        if data.get("List"):
            # if any source has non-empty Data
            for src in data["List"].values():
                entries = src.get("Data") or src.get("data") or []
                if entries:
                    success = True
                    break
        if str(data.get("Status", "")).lower() in ("ok", "success"):
            success = True

        # Deduct 1 credit ONLY on success
        if success:
            c.execute("UPDATE users SET credits = credits - 1 WHERE user_id=?", (user_id,))
            conn.commit()

        # Format & send
        result_text = format_api_response(data, query)

        # Show remaining credits
        c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
        new_credits = c.fetchone()[0]
        result_text += f"\n\n💳 Remaining Credits: {new_credits}"
        await safe_send(update, result_text)

    except Exception as e:
        # Global fallback message required by you
        try:
            if update.callback_query:
                await update.callback_query.answer(ASYNC_ERROR_POPUP, show_alert=True)
            else:
                await safe_send(update, ASYNC_ERROR_POPUP)
        except Exception:
            pass
        print("Lookup exception:", e)

async def num_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_send(update,
            "❌ Usage: /num <number>\n"
            "Example: `/num +919876543210` (must include +91)"
        )
        return
    query = " ".join(context.args).strip()
    await do_lookup(update, query, require_plus91=True)

async def email_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_send(update, "❌ Usage: /email someone@example.com")
        return
    query = " ".join(context.args).strip()
    await do_lookup(update, query, require_plus91=False)

async def name_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_send(update, "❌ Usage: /name First Last")
        return
    query = " ".join(context.args).strip()
    await do_lookup(update, query, require_plus91=False)

# --------- ADMIN: ADD CREDIT ----------
async def add_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await safe_send(update, "❌ You are not authorized to use this command.")
        return

    if len(context.args) < 2:
        await safe_send(update, "❌ Usage: /addcredit <userid> <amount>")
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        c.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (amount, target_id))
        conn.commit()
        await safe_send(update, f"✅ Added {amount} credits to user {target_id}.")
    except Exception as e:
        await safe_send(update, f"⚠️ Error: {e}")

# ===================== INLINE MENU (CALLBACKS) =====================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    # Popups for Manual & Disclaimer (as requested: show in button, not separate text)
    if data == "menu_manual":
        await q.answer(MANUAL_TEXT, show_alert=True)
        return
    if data == "menu_disclaimer":
        await q.answer(DISCLAIMER_TEXT, show_alert=True)
        return

    if data == "menu_credits":
        user_id = update.effective_user.id
        c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        credits_val = row[0] if row else 0
        await q.answer(f"Your credits: {credits_val}", show_alert=True)
        return

    if data == "menu_search":
        await q.message.edit_text("🔍 Choose what to search:", reply_markup=SEARCH_MENU_KB)
        return

    if data == "back_main":
        await q.message.edit_text("📍 Main Menu", reply_markup=MAIN_MENU_KB)
        return

    # Search help prompts
    if data == "search_number":
        await q.answer("Use /num with +91. Example:\n/num +919876543210", show_alert=True)
        return
    if data == "search_email":
        await q.answer("Use /email someone@example.com", show_alert=True)
        return
    if data == "search_name":
        await q.answer("Use /name First Last", show_alert=True)
        return

# ===================== GLOBAL ERROR HANDLER =====================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Show your fixed message to the user
        if isinstance(update, Update) and update.effective_user:
            if update.callback_query:
                await update.callback_query.answer(ASYNC_ERROR_POPUP, show_alert=True)
            else:
                await safe_send(update, ASYNC_ERROR_POPUP)
    except Exception:
        pass
    # Log quietly
    print("Global error:", context.error)

# ===================== MAIN =====================
def main():
    app = Application.builder().token(BOT_TOKEN).connect_timeout(6).read_timeout(20).write_timeout(20).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("credits", credits))
    app.add_handler(CommandHandler("num", num_lookup))
    app.add_handler(CommandHandler("email", email_lookup))
    app.add_handler(CommandHandler("name", name_lookup))
    app.add_handler(CommandHandler("addcredit", add_credit))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(on_callback))

    # Error handler
    app.add_error_handler(on_error)

    print("🤖 Bot is running...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()