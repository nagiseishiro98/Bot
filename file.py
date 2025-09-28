# gun_pair_bot_ready.py
import os
import json
import csv
import logging
from pathlib import Path
from typing import List, Dict
from git import Repo, GitCommandError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)

# --- CONFIG ---
BOT_TOKEN = "7916572802:AAEIyvTaKgQ0_y4DDyMEo3vWF-C1Y9rsQ_w"
GIT_REPO_URL = "https://github.com/nagiseishiro98/Bot.git"

DATA_DIR = Path("data")
REPO_DIR = DATA_DIR / "repo"
ENTRIES_FILE = DATA_DIR / "entries.json"
PAIRS_FILE = DATA_DIR / "pairs.csv"
USED_IDS_FILE = DATA_DIR / "used_ids.json"

# conversation states
(CHOOSING_MYTHIC, CHOOSING_COMMON) = range(2)

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- UTILITIES ---
def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    if not PAIRS_FILE.exists():
        with open(PAIRS_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["mythic_id", "common_id"])
    if not USED_IDS_FILE.exists():
        with open(USED_IDS_FILE, "w") as f:
            json.dump([], f)

def save_entries(entries: List[Dict]):
    with open(ENTRIES_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

def load_entries() -> List[Dict]:
    if not ENTRIES_FILE.exists():
        return []
    with open(ENTRIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_used_ids() -> set:
    if not USED_IDS_FILE.exists():
        return set()
    with open(USED_IDS_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))

def save_used_ids(s: set):
    with open(USED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False, indent=2)

def append_pair(mythic_id: str, common_id: str):
    with open(PAIRS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([mythic_id, common_id])

def parse_txt_file(path: Path) -> List[Dict]:
    res = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                parts = line.split()
            if len(parts) >= 3:
                _id, _name, _hex = parts[0], parts[1], parts[2]
                res.append({"id": str(_id), "name": str(_name), "hex": str(_hex), "source_file": str(path)})
    return res

def load_from_repo_folder(folder: Path) -> List[Dict]:
    entries = []
    for p in folder.rglob("*.txt"):
        try:
            entries += parse_txt_file(p)
        except Exception as e:
            logger.warning("Failed parsing %s : %s", p, e)
    seen = {}
    for e in entries:
        if e["id"] not in seen:
            seen[e["id"]] = e
    return list(seen.values())

def clone_repo(git_url: str, dest: Path) -> None:
    if (dest / ".git").exists():
        repo = Repo(dest)
        try:
            origin = repo.remotes.origin
            origin.pull()
            return
        except Exception as e:
            logger.warning("Pull failed, will reclone: %s", e)
            import shutil
            shutil.rmtree(dest)
            dest.mkdir(parents=True)
    Repo.clone_from(git_url, dest)

def search_entries(entries: List[Dict], q: str, used_ids: set, limit=10) -> List[Dict]:
    q_low = q.lower()
    matched = []
    for e in entries:
        if e["id"] in used_ids:
            continue
        if q_low in e["name"].lower() or q_low == e["id"].lower() or q_low in e["hex"].lower():
            matched.append(e)
        if len(matched) >= limit:
            break
    return matched

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I will help you make Mythic-Common ID pairs from repo .txt files.\n\n"
        "Commands:\n"
        "/list - show number of loaded entries\n"
        "/pair - start interactive pairing (search/select mythic and common)\n"
        "/pairs - list saved pairs\n"
        "/used - list used ids\n"
    )

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entries = load_entries()
    used = load_used_ids()
    total = len(entries)
    used_count = len(used)
    await update.message.reply_text(f"Total entries: {total}\nUsed IDs: {used_count}")

async def pair_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entries = load_entries()
    if not entries:
        await update.message.reply_text("No entries loaded yet. Repo not cloned or empty. Restart bot to auto-load.")
        return ConversationHandler.END
    await update.message.reply_text("Let's create a pair.\nSend a search query for the **Mythic** gun (name or id).")
    return CHOOSING_MYTHIC

async def mythic_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    entries = load_entries()
    used = load_used_ids()
    results = search_entries(entries, query, used, limit=10)
    if not results:
        await update.message.reply_text("No matching unused entries found. Try another search.")
        return CHOOSING_MYTHIC
    buttons = []
    for e in results:
        text = f"{e['name']} ({e['id']})"
        payload = json.dumps({"action": "select_mythic", "id": e["id"]})
        buttons.append([InlineKeyboardButton(text, callback_data=payload)])
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Select Mythic from below:", reply_markup=keyboard)
    return CHOOSING_MYTHIC

async def common_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text.strip()
    entries = load_entries()
    used = load_used_ids()
    results = search_entries(entries, query_text, used, limit=10)
    mythic = context.user_data.get("selected_mythic")
    results = [r for r in results if r["id"] != mythic]
    if not results:
        await update.message.reply_text("No matching unused entries found for Common. Try another search.")
        return CHOOSING_COMMON
    buttons = []
    for e in results:
        text = f"{e['name']} ({e['id']})"
        payload = json.dumps({"action": "select_common", "id": e["id"]})
        buttons.append([InlineKeyboardButton(text, callback_data=payload)])
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Select Common from below:", reply_markup=keyboard)
    return CHOOSING_COMMON

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = json.loads(query.data)
    action = data.get("action")
    selected_id = data.get("id")
    used = load_used_ids()
    if action == "select_mythic":
        if selected_id in used:
            await query.edit_message_text("This ID is already used. Choose another.")
            return CHOOSING_MYTHIC
        context.user_data["selected_mythic"] = selected_id
        await query.edit_message_text(f"Mythic selected: {selected_id}\nSend search for Common gun.")
        return CHOOSING_COMMON
    elif action == "select_common":
        if "selected_mythic" not in context.user_data:
            await query.edit_message_text("No mythic selected. Start /pair again.")
            return ConversationHandler.END
        if selected_id in used or selected_id == context.user_data.get("selected_mythic"):
            await query.edit_message_text("ID already used or same as mythic. Choose another.")
            return CHOOSING_COMMON
        mythic_id = context.user_data.pop("selected_mythic")
        common_id = selected_id
        append_pair(mythic_id, common_id)
        used.add(mythic_id)
        used.add(common_id)
        save_used_ids(used)
        await query.edit_message_text(f"Pair saved:\nMythic: {mythic_id}\nCommon: {common_id}")
        return ConversationHandler.END
    else:
        await query.edit_message_text("Unknown action.")
        return ConversationHandler.END

async def cancel_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("selected_mythic", None)
    await update.message.reply_text("Pairing cancelled.")
    return ConversationHandler.END

async def list_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAIRS_FILE.exists():
        await update.message.reply_text("No pairs saved yet.")
        return
    lines = []
    with open(PAIRS_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for r in reader:
            if r:
                lines.append(f"Mythic: {r[0]}  —  Common: {r[1]}")
    if not lines:
        await update.message.reply_text("No pairs saved yet.")
    else:
        await update.message.reply_text("\n".join(lines[-50:]))

async def used_ids_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    used = sorted(list(load_used_ids()))
    if not used:
        await update.message.reply_text("No used ids yet.")
    else:
        await update.message.reply_text("Used IDs:\n" + ", ".join(used[:200]) + ("" if len(used) <= 200 else "\n(and more...)"))

# --- MAIN ---
def main():
    ensure_dirs()
    # Auto clone repo and load entries at start
    try:
        clone_repo(GIT_REPO_URL, REPO_DIR)
        entries = load_from_repo_folder(REPO_DIR)
        save_entries(entries)
        logger.info(f"Loaded {len(entries)} entries from repo.")
    except Exception as e:
        logger.error("Failed to clone/load repo: %s", e)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("pair", pair_start)],
        states={
            CHOOSING_MYTHIC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mythic_query),
                CallbackQueryHandler(button_handler)
            ],
            CHOOSING_COMMON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, common_query),
                CallbackQueryHandler(button_handler)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_pair)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("pairs", list_pairs))
    app.add_handler(CommandHandler("used", used_ids_cmd))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
