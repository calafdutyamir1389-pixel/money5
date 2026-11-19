"""Broadcast engine: pause/resume, quiet hours, daily limit, ad rotation, stats."""
import asyncio
import logging
import random
import time

from telethon import errors

from . import store, ui
from .core import bot, ctx, user
from .fmt import dur, esc
from .state import st

log = logging.getLogger("tabchi.engine")
data = store.data

# (exception, user-facing reason, permanent)
_REASONS = [
    (errors.ChatWriteForbiddenError, "اجازه‌ی نوشتن ندارد", True),
    (errors.UserBannedInChannelError, "در این گروه محدود شده‌ای", True),
    (errors.ChannelPrivateError, "گروه خصوصی یا حذف شده", True),
    (errors.ChatAdminRequiredError, "فقط ادمین‌ها می‌توانند پیام بدهند", True),
    (errors.UsernameNotOccupiedError, "یوزرنیم پیدا نشد", True),
    (errors.UsernameInvalidError, "یوزرنیم نامعتبر", True),
    (errors.UserIsBlockedError, "کاربر شما را بلاک کرده", True),
    (errors.SlowModeWaitError, "حالت آهسته (اسلو‌مود)", False),
    (ValueError, "هدف پیدا نشد", True),
]


def reason_of(exc):
    for cls, text, permanent in _REASONS:
        if isinstance(exc, cls):
            return text, permanent
    return type(exc).__name__, False


async def notify(text, kind="info"):
    if kind == "finish" and not data["notify_finish"]:
        return
    if kind == "error" and not data["notify_error"]:
        return
    try:
        await bot.send_message(ctx.owner, text, link_preview=False)
    except Exception as e:
        log.warning("notify failed: %s", e)


# ---------------------------------------------------------------- control
def can_start():
    if st.active:
        return False, "ارسال از قبل در حال اجراست."
    if not store.enabled_ads():
        return False, "اول یک تبلیغ فعال ثبت کن."
    if not data["targets"]:
        return False, "لیست اهداف خالی است."
    return True, ""


def start():
    st.reset()
    st.running = True
    st.paused = False
    st.started = time.time()
    st.task = asyncio.create_task(run_broadcast())


def stop():
    if st.running:
        st.stop_reason = "stopped"
        st.running = False
        st.paused = False


# ---------------------------------------------------------------- helpers
async def nap(seconds):
    left = float(seconds)
    while left > 0 and st.running:
        await asyncio.sleep(1)
        if not st.paused:
            left -= 1


def in_active_hours():
    if not data["hours_on"]:
        return True
    h, a, b = store.now_local().hour, data["hours_from"], data["hours_to"]
    if a == b:
        return True
    return a <= h < b if a < b else (h >= a or h < b)


async def gate():
    """Wait until sending is allowed. False if the run was stopped meanwhile."""
    while st.running:
        if st.paused:
            st.waiting = "⏸ مکث"
        elif not in_active_hours():
            st.waiting = "🌙 خارج از ساعت فعال"
        elif data["daily_limit"] and store.day_sent() >= data["daily_limit"]:
            st.waiting = "📅 سقف روزانه پر شده؛ ادامه از فردا"
            if not st.limit_notified:
                st.limit_notified = True
                await notify("📅 سقف روزانه (%d) پر شد. ارسال از فردا ادامه پیدا می‌کند." % data["daily_limit"], "info")
        else:
            st.waiting = ""
            st.limit_notified = False
            return True
        await asyncio.sleep(2)
    return False


async def load_ads():
    ready, missing = [], []
    for ad in store.enabled_ads():
        try:
            msg = await user.get_messages("me", ids=ad["msg_id"])
        except Exception as e:
            log.warning("get ad failed: %s", e)
            msg = None
        (ready if msg else missing).append((ad, msg) if msg else ad)
    if missing:
        names = "، ".join(esc(a["name"]) for a in missing)
        await notify("⚠️ این تبلیغ‌ها در Saved Messages پیدا نشدند و کنار گذاشته شدند:\n" + names, "error")
    return ready[:1] if data["ad_mode"] == "single" else ready


def choose(ready):
    if data["ad_mode"] == "random":
        return random.choice(ready)
    return ready[st.counter % len(ready)]


async def send_one(target, msg):
    """Returns (ok, reason, permanent). Raises PeerFloodError."""
    for _ in range(2):
        try:
            entity = await user.get_entity(target)
            await user.send_message(entity, msg)
            return True, "", False
        except errors.FloodWaitError as e:
            await notify("⏳ تلگرام %d ثانیه محدودیت داد؛ صبر می‌کنم." % e.seconds, "error")
            st.waiting = "⏳ محدودیت تلگرام (%d ثانیه)" % e.seconds
            await nap(e.seconds + 5)
            st.waiting = ""
            if not st.running:
                return False, "متوقف شد", False
        except errors.PeerFloodError:
            raise
        except Exception as e:
            text, permanent = reason_of(e)
            if text == type(e).__name__:
                log.warning("send to %s failed: %r", target, e)
            return False, text, permanent
    return False, "محدودیت تلگرام", False


def record(target, ad, ok, reason, permanent):
    title = store.title_of(target)
    inf = data["target_info"].setdefault(
        str(target), {"title": title, "kind": "", "sent": 0, "fails": 0, "last_error": ""})
    if ok:
        st.sent += 1
        inf["sent"] += 1
        inf["fails"] = 0
        inf["last_error"] = ""
        ad["sent"] += 1
        st.counter += 1          # rotate only after a successful send
        store.bump_sent()
    else:
        st.failed += 1
        inf["last_error"] = reason
        data["stats"]["failed"] += 1
        if permanent:
            inf["fails"] += 1
        st.errors.append((title, reason))
        del st.errors[:-200]
        limit = data["auto_remove"]
        if limit and permanent and inf["fails"] >= limit:
            store.remove_target(target)
            st.removed.append(title)
    store.save()


# ---------------------------------------------------------------- main loop
async def run_broadcast():
    try:
        while st.running:
            st.round += 1
            ready = await load_ads()
            if not ready:
                st.stop_reason = "no_ad"
                await notify("❌ تبلیغ فعالی در Saved Messages پیدا نشد. دوباره ثبتش کن.", "error")
                break
            targets = list(data["targets"])
            if data["shuffle"]:
                random.shuffle(targets)
            st.total, st.done = len(targets), 0
            for i, target in enumerate(targets):
                if not st.running:
                    break
                if target not in data["targets"]:      # removed meanwhile
                    st.done += 1
                    continue
                if not await gate():
                    break
                st.current = store.title_of(target)
                ad, msg = choose(ready)
                try:
                    ok, why, perm = await send_one(target, msg)
                except errors.PeerFloodError:
                    st.stop_reason = "peerflood"
                    st.running = False
                    await notify("🚫 تلگرام خطای PeerFlood داد (اکانت محدود شده). ارسال متوقف شد.", "error")
                    break
                record(target, ad, ok, why, perm)
                st.done += 1
                if i < len(targets) - 1 or data["loop"]:
                    await nap(random.uniform(data["delay_min"], data["delay_max"]))
            if not data["loop"] or not st.running:
                break
            st.waiting = "🔁 دور بعدی تا %s دیگر" % dur(data["loop_gap_min"] * 60)
            await nap(data["loop_gap_min"] * 60)
            st.waiting = ""
    except Exception as e:
        log.exception("broadcast crashed")
        st.stop_reason = "error"
        await notify("❌ خطای داخلی: %s" % esc(repr(e)), "error")
    finally:
        st.running = False
        st.paused = False
        st.waiting = ""
        st.current = ""
        last = {"start": st.started, "end": time.time(), "sent": st.sent,
                "failed": st.failed, "rounds": st.round, "reason": st.stop_reason,
                "errors": [list(e) for e in st.errors[-50:]], "removed": list(st.removed)}
        data["last_run"] = last
        data["stats"]["runs"] += 1
        store.save()
        await notify(ui.finish_text(last), "finish")
