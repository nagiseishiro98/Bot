# bot_category_ui.py
import os
import json
import logging
from pathlib import Path
from git import Repo, GitCommandError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, MessageHandler, ContextTypes,
    ConversationHandler, filters
)

# --- CONFIG ---
BOT_TOKEN = "7916572802:AAEIyvTaKgQ0_y4DDyMEo3vWF-C1Y9rsQ_w"
GIT_REPO_URL = "https://github.com/nagiseishiro98/Bot.git"

DATA_DIR = Path("data")
REPO_DIR = DATA_DIR / "repo"
USED_IDS_FILE = DATA_DIR / "used_ids.json"
PAIRS_FILE = DATA_DIR / "pairs.json"

# --- STATES ---
SELECT_CATEGORY, SEARCH_MYTHIC, SELECT_MYTHIC, SEARCH_COMMON, SELECT_COMMON, CONFIRM_PAIR = range(6)

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- UTILITIES ---
def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USED_IDS_FILE.exists():
        json.dump([], USED_IDS_FILE.open("w"))
    if not PAIRS_FILE.exists():
        json.dump([], PAIRS_FILE.open("w"))

def clone_repo():
    if REPO_DIR.exists() and (REPO_DIR / ".git").exists():
        repo = Repo(REPO_DIR)
        repo.remotes.origin.pull()
    else:
        if REPO_DIR.exists():
            import shutil
            shutil.rmtree(REPO_DIR)
        Repo.clone_from(GIT_REPO_URL, REPO_DIR)

def load_categories():
    return [f.name for f in REPO_DIR.glob("*.txt")]

def parse_txt(path):
    entries = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        entries.append({"id": parts[0], "name": parts[1], "hex": parts[2]})
    return entries

def load_entries(category):
    path = REPO_DIR / category
    return parse_txt(path)

def load_used_ids():
    if not USED_IDS_FILE.exists():
        return set()
    return set(json.load(USED_IDS_FILE.open()))

def save_used_ids(ids_set):
    with USED_IDS_FILE.open("w") as f:
        json.dump(list(ids_set), f)

def load_pairs():
    if not PAIRS_FILE.exists():
        return []
    return json.load(PAIRS_FILE.open())

def save_pair(category, mythic, common):
    pairs = load_pairs()
    pairs.append({"category": category, "mythic": mythic, "common": common})
    with PAIRS_FILE.open("w") as f:
        json.dump(pairs, f, indent=2)

def filter_entries(entries, query, used_ids):
    query = query.lower()
    return [e for e in entries if query in e["name"].lower() or query in e["id"] and e["id"] not in used_ids]

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_dirs()
    clone_repo()
    categories = load_categories()
    buttons = [[InlineKeyboardButton(c, callback_data=f"cat|{c}")] for c in categories]
    await update.message.reply_text("Select a category (TXT file) to create pairs:", reply_markup=InlineKeyboardMarkup(buttons))

# CATEGORY selection
async def category_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split("|")[1]
    context.user_data["category"] = category
    context.user_data["entries"] = load_entries(category)
    context.user_data["used_ids"] = load_used_ids()
    await show_search_mythic(query, context)
    return SEARCH_MYTHIC

async def show_search_mythic(query, context):
    buttons = [[InlineKeyboardButton("Search 🔍", callback_data="search_mythic")]]
    # show top 5 unused entries
    cnt = 0
    for e in context.user_data["entries"]:
        if e["id"] in context.user_data["used_ids"]:
            continue
        buttons.append([InlineKeyboardButton(f"{e['name']} ({e['id']})", callback_data=f"mythic|{e['id']}")])
        cnt += 1
        if cnt >= 5:
            break
    buttons.append([InlineKeyboardButton("Cancel ❌", callback_data="cancel")])
    await query.edit_message_text("Select Mythic gun or Search:", reply_markup=InlineKeyboardMarkup(buttons))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    used_ids = context.user_data.get("used_ids", set())

    if data == "cancel":
        await query.edit_message_text("Pairing cancelled.")
        context.user_data.clear()
        return ConversationHandler.END

    if data.startswith("cat|"):
        return await category_select(update, context)

    if data == "search_mythic":
        await query.edit_message_text("Send me part of the name or id to search Mythic gun:")
        return SEARCH_MYTHIC

    if data.startswith("mythic|"):
        mythic_id = data.split("|")[1]
        if mythic_id in used_ids:
            await query.edit_message_text("ID already used, choose another.")
            return SEARCH_MYTHIC
        context.user_data["selected_mythic"] = mythic_id
        await show_search_common(query, context)
        return SEARCH_COMMON

    if data == "search_common":
        await query.edit_message_text("Send me part of the name or id to search Common gun:")
        return SEARCH_COMMON

    if data.startswith("common|"):
        common_id = data.split("|")[1]
        mythic_id = context.user_data.get("selected_mythic")
        category = context.user_data.get("category")
        # check duplicate pair
        pairs = load_pairs()
        for p in pairs:
            if (p["mythic"] == mythic_id and p["common"] == common_id) or (p["mythic"] == common_id and p["common"] == mythic_id):
                await query.edit_message_text("This pair already exists globally!")
                return SEARCH_COMMON
        # save pair
        save_pair(category, mythic_id, common_id)
        # mark IDs used
        used_ids.update([mythic_id, common_id])
        save_used_ids(used_ids)
        await query.edit_message_text(f"Pair saved!\nMythic: {mythic_id}\nCommon: {common_id}")
        context.user_data.clear()
        return ConversationHandler.END

    return ConversationHandler.END

async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "selected_mythic" not in context.user_data:
        # search mythic
        results = filter_entries(context.user_data["entries"], text, context.user_data["used_ids"])
        buttons = [[InlineKeyboardButton(f"{e['name']} ({e['id']})", callback_data=f"mythic|{e['id']}")] for e in results[:10]]
        buttons.append([InlineKeyboardButton("Cancel ❌", callback_data="cancel")])
        await update.message.reply_text("Select Mythic:", reply_markup=InlineKeyboardMarkup(buttons))
        return SEARCH_MYTHIC
    else:
        # search common
        results = filter_entries(context.user_data["entries"], text, context.user_data["used_ids"])
        mythic_id = context.user_data["selected_mythic"]
        results = [e for e in results if e["id"] != mythic_id]
        buttons = [[InlineKeyboardButton(f"{e['name']} ({e['id']})", callback_data=f"common|{e['id']}")] for e in results[:10]]
        buttons.append([InlineKeyboardButton("Cancel ❌", callback_data="cancel")])
        await update.message.reply_text("Select Common:", reply_markup=InlineKeyboardMarkup(buttons))
        return SEARCH_COMMON

def main():
    ensure_dirs()
    clone_repo()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.COMMAND & ~filters.Regex("start"), start)],
        states={
            SEARCH_MYTHIC: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_text)
            ],
            SEARCH_COMMON: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_text)
            ],
        },
        fallbacks=[],
        allow_reentry=True
    )

    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex("start"), start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
