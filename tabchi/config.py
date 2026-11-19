"""Environment variables and constants."""
import os
import sys


def _req(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit("❌ متغیر محیطی %s تنظیم نشده است." % name)
    return value


API_ID = int(_req("API_ID"))
API_HASH = _req("API_HASH")
SESSION_STRING = _req("SESSION_STRING")
BOT_TOKEN = _req("BOT_TOKEN")
# Telegram numeric id of the owner (the only person who can use the bot).
# Optional: if empty, the userbot account itself is the owner.
_owner = os.environ.get("OWNER_ID", "").strip()
if _owner and not _owner.lstrip("-").isdigit():
    sys.exit("❌ OWNER_ID باید آیدی عددی باشد (مثلا 123456789).")
OWNER_ID = int(_owner) if _owner else None
DATA_PATH = os.environ.get("DATA_PATH", "data.json")

# Colored buttons (blue/green/red). On by default; automatically skipped if the
# installed Telethon can't carry them. Set COLOR_BUTTONS=0 to force plain buttons.
COLOR_BUTTONS = os.environ.get("COLOR_BUTTONS", "1").strip() != "0"
CREDIT = "ربات تبچی حرفه‌ای ساخته شده توسط امیر @am_1660"
VERSION = "2.0"
NO_ACCESS = "⛔ شما دسترسی ندارید."
PAGE_SIZE = 8
MAX_IMPORT = 300
