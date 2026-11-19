"""Telegram event handlers: panel callbacks, owner private chat, Saved Messages."""
import asyncio
import io
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from telethon import events, errors
from telethon.errors import MessageNotModifiedError

from . import config, engine, store, targets, ui
from .core import bot, ctx, user
from .fmt import en, esc, fa, norm_text
from .state import st

log = logging.getLogger("tabchi.handlers")
data = store.data


@dataclass
class R:
    """Result of a callback route: optional toast and optional screen to render."""
    screen: Optional[ui.Screen] = None
    toast: str = ""
    alert: bool = False


# ---------------------------------------------------------------- rendering
def _key(event):
    try:
        chat = event.chat_id
    except Exception:
        chat = None
    return (chat, str(event.query.msg_id))


def _is_inline(event):
    return type(event.query.msg_id).__name__.startswith("InputBotInlineMessageID")


async def _raw_inline_edit(event, screen):
    """Fallback for inline messages when event.edit can't handle the id type."""
    from telethon.extensions import html as tlhtml
    from telethon.tl import functions
    msg_id = event.query.msg_id
    plain, entities = tlhtml.parse(screen.text)
    request = functions.messages.EditInlineBotMessageRequest(
        id=msg_id, message=plain, no_webpage=True, entities=entities,
        reply_markup=bot.build_reply_markup(screen.buttons))
    dc = getattr(msg_id, "dc_id", None)
    if dc and dc != bot.session.dc_id:
        sender = await bot._borrow_exported_sender(dc)
        try:
            return await bot._call(sender, request)
        finally:
            await bot._return_exported_sender(sender)
    return await bot(request)


async def edit_event(event, screen, _retry=True):
    try:
        await event.edit(screen.text, buttons=ui.markup(screen.buttons), link_preview=False)
    except MessageNotModifiedError:
        return
    except Exception as first:
        if _retry and ui.strip_styles(screen.buttons):
            log.warning("colored buttons rejected (%r); falling back to plain buttons", first)
            return await edit_event(event, screen, _retry=False)
        if not _is_inline(event):
            raise
        log.info("event.edit failed (%r); using raw inline edit", first)
        try:
            await _raw_inline_edit(event, screen)
        except MessageNotModifiedError:
            pass


async def render(event, screen):
    await edit_event(event, screen)
    key = _key(event)
    if screen.name == "main":
        st.live = {"edit": lambda scr, ev=event: edit_event(ev, scr),
                   "key": key, "last": screen.text}
    elif st.live and st.live["key"] == key:
        st.live = None


async def send_screen(screen):
    try:
        try:
            msg = await bot.send_message(ctx.owner, screen.text, buttons=ui.markup(screen.buttons), link_preview=False)
        except Exception as first:
            if not ui.strip_styles(screen.buttons):
                raise
            log.warning("colored buttons rejected (%r); falling back to plain buttons", first)
            msg = await bot.send_message(ctx.owner, screen.text, buttons=ui.markup(screen.buttons), link_preview=False)
        if not msg.reply_markup:
            log.warning("panel was sent WITHOUT buttons (reply_markup empty)")
    except Exception as e:
        log.exception("send_screen failed")
        await bot.send_message(ctx.owner, "❌ ساخت پنل با دکمه‌ها خطا داد:\n<code>%s</code>" % esc(repr(e)[:500]))
        raise
    if screen.name == "main":
        st.live = {"edit": lambda scr, m=msg: m.edit(scr.text, buttons=ui.markup(scr.buttons), link_preview=False),
                   "key": (msg.chat_id, str(msg.id)), "last": screen.text}
    return msg


async def live_ticker():
    """Refresh the main panel automatically while it is on screen."""
    while True:
        await asyncio.sleep(5)
        lv = st.live
        if not lv:
            continue
        scr = ui.main_screen()
        if scr.text == lv["last"]:
            continue
        try:
            await lv["edit"](scr)
            lv["last"] = scr.text
        except MessageNotModifiedError:
            lv["last"] = scr.text
        except errors.FloodWaitError:
            pass
        except Exception as e:
            log.debug("live edit failed: %s", e)
            if st.live is lv:
                st.live = None


# ---------------------------------------------------------------- inline
@bot.on(events.InlineQuery)
async def inline_handler(event):
    if event.sender_id != ctx.owner:
        await event.answer([])
        return
    scr = ui.main_screen()
    for attempt in (1, 2):
        try:
            result = event.builder.article(
                title=data["open_text"], description="برای باز کردن پنل بزنید",
                text=scr.text, buttons=ui.markup(scr.buttons), parse_mode="html", link_preview=False)
            await event.answer([result], cache_time=0, private=True)
            return
        except Exception:
            log.exception("inline panel failed (attempt %d)", attempt)
            if attempt == 1 and ui.strip_styles(scr.buttons):
                log.warning("retrying inline panel with plain buttons")
                continue
            break
    await event.answer([])


# ---------------------------------------------------------------- callback routes
async def r_noop(event, a):
    return R()


async def r_go(event, a):
    name = a[0]
    if name == "tlist":
        return R(ui.targets_list_screen(int(a[1])))
    if name == "groups":
        if not st.groups:
            try:
                await targets.load_groups()
            except Exception as e:
                log.warning("load groups failed: %s", e)
        return R(ui.groups_screen(int(a[1])))
    if name == "help":
        return R(ui.help_screen(a[1] if len(a) > 1 else None))
    if name == "sys":
        return R(ui.sys_screen(ctx.owner, ctx.bot_username))
    toast = "✅ بروزرسانی شد" if name == "main" else ""
    return R(ui.SCREENS[name](), toast)


async def r_run(event, a):
    act = a[0]
    if act == "start":
        ok, msg = engine.can_start()
        if not ok:
            return R(None, "⚠️ " + msg, True)
        engine.start()
        await asyncio.sleep(0.3)
        return R(ui.main_screen(), "▶️ ارسال شروع شد")
    if act == "pause":
        st.paused = True
        return R(ui.main_screen(), "⏸ مکث شد")
    if act == "resume":
        st.paused = False
        return R(ui.main_screen(), "▶️ ادامه پیدا کرد")
    engine.stop()
    return R(ui.main_screen(), "⏹ در حال توقف…")


async def r_ad(event, a):
    act = a[0]
    if act == "how":
        return R(None, "پیام تبلیغ را در Saved Messages بفرست، روی آن ریپلای کن و بنویس: تنظیم تبلیغ", True)
    if act == "mode":
        store.cycle_mode()
        return R(ui.ads_screen())
    if act == "prev":
        ads = store.enabled_ads()
        if not ads:
            return R(None, "تبلیغ فعالی وجود ندارد.", True)
        sent = 0
        for ad in ads:
            msg = await user.get_messages("me", ids=ad["msg_id"])
            if msg:
                await user.send_message("me", msg)
                sent += 1
        return R(None, "پیش‌نمایش %s تبلیغ در Saved Messages فرستاده شد." % fa(sent), True)
    ad_id = int(a[1])
    if act == "tog":
        ad = store.get_ad(ad_id)
        if ad:
            ad["enabled"] = not ad["enabled"]
            store.save()
        return R(ui.ads_screen())
    if act == "del":
        ad = store.get_ad(ad_id)
        name = esc(ad["name"]) if ad else ""
        return R(ui.confirm_screen("تبلیغ «%s» حذف شود؟" % name, "ad:delyes:%d" % ad_id, "go:ads"))
    if act == "delyes":
        store.remove_ad(ad_id)
        return R(ui.ads_screen(), "🗑 تبلیغ حذف شد")
    return R()


async def r_t(event, a):
    act = a[0]
    if act == "rm":
        page, key = int(a[1]), ":".join(a[2:])
        t = store.find_target(key)
        if t is not None:
            store.remove_target(t)
            store.save()
        return R(ui.targets_list_screen(page), "🗑 حذف شد")
    if act == "clearask":
        return R(ui.confirm_screen("همه‌ی %s هدف پاک شوند؟" % fa(len(data["targets"])), "t:clearyes", "go:targets"))
    if act == "clearyes":
        store.clear_targets()
        return R(ui.targets_screen(), "🗑 لیست اهداف پاک شد")
    if act == "purge":
        bad = [t for t in data["targets"] if store.info_of(t).get("fails", 0) >= ui.PROBLEM_FAILS]
        for t in bad:
            store.remove_target(t)
        store.save()
        return R(ui.targets_screen(), "🧹 %s هدف مشکل‌دار حذف شد" % fa(len(bad)), True)
    if act == "export":
        if not data["targets"]:
            return R(None, "لیست اهداف خالی است.", True)
        lines = ["%s  # %s" % (t, store.title_of(t)) for t in data["targets"]]
        await send_text_file("targets.txt", "\n".join(lines), "📤 خروجی لیست اهداف")
        return R(None, "فایل در پیوی ربات ارسال شد ✅")
    if act == "errfile":
        rows = [(t, store.info_of(t)) for t in data["targets"] if store.info_of(t).get("last_error")]
        if not rows:
            return R(None, "هدف دارای خطا وجود ندارد 🎉", True)
        lines = ["%s | %s | %s | %s" % (store.title_of(t), t, i["last_error"], i.get("fails", 0)) for t, i in rows]
        await send_text_file("errors.txt", "\n".join(lines), "📄 اهداف دارای خطا")
        return R(None, "فایل در پیوی ربات ارسال شد ✅")
    return R()


async def r_g(event, a):
    act, page = a[0], int(a[1])
    if act == "tog":
        gid = int(a[2])
        if gid in data["targets"]:
            store.remove_target(gid)
        else:
            title = next((g["title"] for g in st.groups if g["id"] == gid), str(gid))
            store.add_target(gid, title, "group")
        store.save()
        return R(ui.groups_screen(page))
    if act == "all":
        n = 0
        for g in st.groups:
            if store.add_target(g["id"], g["title"], "group"):
                n += 1
        store.save()
        return R(ui.groups_screen(page), "%s گروه اضافه شد" % fa(n))
    if act == "reload":
        await targets.load_groups()
        return R(ui.groups_screen(page), "🔄 بازخوانی شد")
    return R()


async def r_set(event, a):
    if a[0] in store.STEPS:
        store.step(a[0], int(a[1]))
    return R(ui.SCREENS["safety" if a[0] in ("daily_limit", "auto_remove") else "timing"]())


async def r_tog(event, a):
    if a[0] in store.TOGGLES:
        store.toggle(a[0])
    return R(ui.SCREENS["safety" if a[0] in ("shuffle", "notify_finish", "notify_error") else "timing"]())


PROMPTS = {
    "addt": ("🎯 <b>افزودن هدف</b>\nاهداف را بفرست (هر خط یکی):\n"
             "<code>@username</code> · لینک <code>t.me/…</code> · آیدی عددی\n"
             "یا یک فایل <code>.txt</code> بفرست. (حداکثر %s هدف)" % fa(config.MAX_IMPORT), "targets"),
    "delay": ("⏱ <b>تنظیم فاصله</b>\nحداقل و حداکثر (ثانیه) را بفرست، مثلا:\n<code>30 60</code>", "timing"),
    "trigger": ("⌨️ <b>کلمه‌ی باز کردن پنل</b>\nکلمه یا عبارت جدید را بفرست، مثلا:\n<code>پنل تبچی</code>\n"
                "این کلمه در <b>Saved Messages</b> و <b>پیوی ربات</b> پنل را باز می‌کند.", "custom"),
    "title": ("🖋 <b>عنوان پنل</b>\nمتن جدید را بفرست.", "custom"),
    "opentext": ("💬 <b>متن بازشدن</b>\nمتن جدید را بفرست.", "custom"),
    "restore": ("♻️ <b>ریستور</b>\nفایل بکاپ (JSON) را بفرست.\n⚠️ تنظیمات فعلی جایگزین می‌شود.", "tools"),
    "test": ("🧪 <b>تست هدف</b>\nیوزرنیم، لینک یا آیدی یک هدف را بفرست؛ اولین تبلیغ فعال به آن ارسال می‌شود.", "tools"),
}


async def r_ask(event, a):
    kind = a[0]
    if kind == "restore" and st.running:
        return R(None, "اول ارسال را متوقف کن.", True)
    if kind == "test" and not store.enabled_ads():
        return R(None, "اول یک تبلیغ فعال ثبت کن.", True)
    await cleanup_prompt()
    text, back_to = PROMPTS[kind]
    from .ui import cbtn
    msg = await bot.send_message(ctx.owner, text, buttons=ui.markup([[cbtn("❌ انصراف", "cancel", "danger")]]))
    st.pending = {"state": kind, "back": back_to, "prompt": msg}
    return R(None, "به پیوی ربات برو ✅")


async def r_cancel(event, a):
    st.pending = {}
    try:
        await event.delete()
    except Exception:
        pass
    return R(None, "انصراف داده شد")


async def r_tool(event, a):
    act = a[0]
    if act == "backup":
        store.save()
        name = "tabchi-backup-%s.json" % store.now_local().strftime("%Y%m%d-%H%M")
        await send_text_file(name, json.dumps(data, ensure_ascii=False, indent=2), "💾 بکاپ تنظیمات")
        return R(None, "بکاپ در پیوی ربات ارسال شد ✅")
    if act == "statsask":
        return R(ui.confirm_screen("همه‌ی آمارها صفر شوند؟", "tool:statsyes", "go:stats"))
    if act == "statsyes":
        store.reset_stats()
        return R(ui.stats_screen(), "♻️ آمار ریست شد")
    return R()


ROUTES = {"noop": r_noop, "go": r_go, "run": r_run, "ad": r_ad, "t": r_t, "g": r_g,
          "set": r_set, "tog": r_tog, "ask": r_ask, "cancel": r_cancel, "tool": r_tool}


@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ctx.owner:
        await event.answer("⛔ دسترسی ندارید", alert=True)
        return
    try:
        parts = event.data.decode("utf-8").split(":")
        route = ROUTES.get(parts[0])
        res = await route(event, parts[1:]) if route else R()
    except Exception:
        log.exception("callback failed: %r", event.data)
        await event.answer("❌ خطای غیرمنتظره؛ لاگ را ببین.", alert=True)
        return
    await event.answer(res.toast or None, alert=res.alert)
    if res.screen:
        try:
            await render(event, res.screen)
        except Exception as e:
            log.warning("render failed: %s", e)


# ---------------------------------------------------------------- helpers
async def send_text_file(name, content, caption):
    f = io.BytesIO(content.encode("utf-8"))
    f.name = name
    await bot.send_file(ctx.owner, f, caption=caption, force_document=True)


async def cleanup_prompt():
    prompt = st.pending.get("prompt")
    st.pending = {}
    if prompt:
        try:
            await prompt.delete()
        except Exception:
            pass


async def read_lines(event, text):
    if event.document:
        raw = await event.download_media(bytes)
        return raw.decode("utf-8", errors="ignore").splitlines()
    return text.splitlines()


# ---------------------------------------------------------------- input flows
async def in_addt(event, text):
    notice = await event.respond("⏳ در حال بررسی اهداف…")
    res = await targets.add_many(await read_lines(event, text))
    try:
        await notice.delete()
    except Exception:
        pass
    out = ["✅ <b>%s</b> هدف اضافه شد." % fa(res["added"])]
    if res["dup"]:
        out.append("♻️ تکراری: %s" % fa(res["dup"]))
    if res["flood"]:
        out.append("⏳ تلگرام محدودیت داد؛ %s ثانیه بعد ادامه بده." % fa(res["flood"]))
    if res["bad"]:
        out.append("⚠️ نامعتبر یا پیدا نشد (%s):\n%s" % (
            fa(len(res["bad"])), esc("\n".join(res["bad"][:15]))))
    return True, "\n".join(out)


async def in_delay(event, text):
    try:
        a, b = [int(x) for x in en(text).split()[:2]]
        if a < 1 or b < a:
            raise ValueError
    except ValueError:
        return False, "فرمت اشتباه است. مثال: <code>30 60</code>"
    data["delay_min"], data["delay_max"] = a, b
    store.save()
    return True, "✅ فاصله روی %s تا %s ثانیه تنظیم شد." % (fa(a), fa(b))


async def in_title(event, text):
    if not text:
        return False, "متن خالی است."
    data["panel_title"] = text[:100]
    store.save()
    return True, "✅ عنوان پنل تغییر کرد."


async def in_trigger(event, text):
    word = " ".join(text.split())
    if not 2 <= len(word) <= 30 or word.startswith("/"):
        return False, "کلمه باید ۲ تا ۳۰ حرف باشد و با / شروع نشود."
    data["trigger"] = word
    store.save()
    return True, "✅ از حالا با نوشتن <code>%s</code> پنل باز می‌شود." % esc(word)


async def in_opentext(event, text):
    if not text:
        return False, "متن خالی است."
    data["open_text"] = text[:60]
    store.save()
    return True, "✅ متن بازشدن تغییر کرد."


async def in_restore(event, text):
    if not event.document:
        return False, "فایل JSON بکاپ را بفرست."
    try:
        raw = await event.download_media(bytes)
        n = store.restore(json.loads(raw.decode("utf-8")))
    except Exception:
        return False, "❌ فایل نامعتبر است."
    return True, "✅ تنظیمات ریستور شد (%s مورد)." % fa(n)


async def in_test(event, text):
    t = targets.norm_target(text)
    if t is None:
        return False, "هدف نامعتبر است."
    ads = store.enabled_ads()
    try:
        msg = await user.get_messages("me", ids=ads[0]["msg_id"])
    except Exception:
        msg = None
    if not msg:
        return True, "❌ تبلیغ در Saved Messages پیدا نشد."
    try:
        ok, why, _ = await engine.send_one(t, msg)
    except errors.PeerFloodError:
        return True, "🚫 PeerFlood: اکانت محدود شده است."
    return True, "✅ تبلیغ به %s ارسال شد." % esc(t) if ok else "❌ ارسال نشد: %s" % esc(why)


INPUTS = {"addt": in_addt, "delay": in_delay, "title": in_title, "trigger": in_trigger,
          "opentext": in_opentext, "restore": in_restore, "test": in_test}


# ---------------------------------------------------------------- owner private chat
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def pv_handler(event):
    """Never fail silently: any error is logged and (for the owner) shown in the chat."""
    log.info("PV message from %s (owner=%s)", event.sender_id, ctx.owner)
    try:
        await _pv_inner(event)
    except Exception as e:
        log.exception("pv_handler failed")
        if event.sender_id == ctx.owner:
            try:
                await event.respond("❌ خطا:\n<code>%s</code>" % esc(repr(e)[:700]))
            except Exception:
                pass


async def _pv_inner(event):
    text = (event.raw_text or "").strip()
    if event.sender_id != ctx.owner:
        # Anyone except the owner: tell them they have no access.
        if text.startswith("/start"):
            await event.respond(config.NO_ACCESS)
        return

    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd:
        await cleanup_prompt()
        if cmd == "/start":
            await send_screen(ui.main_screen())
        elif cmd == "/debug":
            try:
                import telethon
                from telethon.tl.alltlobjects import LAYER
                m = await bot.send_message(ctx.owner, "🛠 Telethon %s · layer %s\nدکمه‌ی تست:" % (telethon.__version__, LAYER),
                                           buttons=ui.markup([[ui.cbtn("تست ✅", "noop")]]))
                await event.respond("reply_markup: %s\nدکمه‌ی رنگی: %s (COLOR_BUTTONS=%s)" % (
                    "OK" if m.reply_markup else "EMPTY ❌",
                    "پشتیبانی می‌شود ✅" if ui.colors_supported() else "پشتیبانی نمی‌شود ❌",
                    "1" if config.COLOR_BUTTONS else "0"))
            except Exception as e:
                await event.respond("❌ <code>%s</code>" % esc(repr(e)[:800]))
        elif cmd in ("/panel", "/status"):
            await send_screen(ui.main_screen())
        elif cmd == "/help":
            await send_screen(ui.help_screen())
        elif cmd == "/stop":
            engine.stop()
            await event.respond("⏹ در حال توقف…" if st.active else "ارسالی در حال اجرا نیست.")
        elif cmd != "/cancel":
            await event.respond("دستور ناشناخته. /panel را بزن.")
        return

    state = st.pending.get("state")
    if is_trigger(text) and state not in ("title", "opentext", "trigger"):
        await cleanup_prompt()
        await send_screen(ui.main_screen())
        return
    if not state:
        await event.respond("برای باز کردن پنل بنویس: <code>%s</code> یا /panel" % esc(data["trigger"]))
        return

    done, message = await INPUTS[state](event, text)
    if not done:
        await event.respond(message)
        return
    back_to = st.pending.get("back", "main")
    await cleanup_prompt()
    try:
        await event.delete()
    except Exception:
        pass
    await bot.send_message(ctx.owner, message, link_preview=False)
    await send_screen(ui.SCREENS[back_to]())


# ---------------------------------------------------------------- Saved Messages (userbot)
def _ad_name(msg):
    text = " ".join((msg.raw_text or "").split())
    if text:
        return text[:40]
    for attr, label in (("photo", "📷 عکس"), ("video", "🎬 ویدیو"), ("voice", "🎤 ویس"),
                        ("sticker", "🏷 استیکر"), ("document", "📎 فایل")):
        if getattr(msg, attr, None):
            return label
    return "تبلیغ"


def is_trigger(text):
    """True if text is the configurable open-panel word (or plain 'panel')."""
    n = norm_text(text)
    return bool(n) and n in (norm_text(data["trigger"]), "panel")


NO_INLINE_HINT = ("⚠️ <b>حالت اینلاین ربات فعال نیست</b>\n"
                  "بدون آن پنل در Saved Messages باز نمی‌شود (در پیوی ربات کار می‌کند).\n"
                  "در @BotFather بزن: <code>/setinline</code> ← انتخاب ربات ← یک متن کوتاه (مثلا <code>پنل</code>).")


async def open_panel_in_chat(event):
    """Post the panel into the current chat (Saved Messages or any PV the owner
    is typing in) via inline mode, then delete the owner's trigger message.
    Falls back to sending the panel in the bot's own PV if that fails."""
    peer = event.chat_id
    try:
        results = await user.inline_query(ctx.bot_username, "panel")
        if not results:
            raise RuntimeError("inline query returned no results")
        await results[0].click(peer)
        await event.delete()
    except Exception as e:
        log.warning("opening panel in chat %s failed: %r", peer, e)
        await send_screen(ui.main_screen())
        if peer != ctx.owner:
            try:
                await event.delete()
            except Exception:
                pass
        else:
            await event.reply("⚠️ پنل اینجا باز نشد؛ در پیوی ربات برایت فرستادم. "
                              "برای باز شدن در Saved Messages و پیوی‌های دیگر، حالت اینلاین ربات را در BotFather فعال کن (/setinline).")


@user.on(events.NewMessage(chats="me", outgoing=True))
async def saved_handler(event):
    t = (event.raw_text or "").strip()
    if is_trigger(t):
        await open_panel_in_chat(event)
    elif t in ("تنظیم تبلیغ", "/setad", "افزودن تبلیغ") and event.is_reply:
        replied = await event.get_reply_message()
        ad, status = store.add_ad(replied.id, _ad_name(replied))
        if status == "full":
            await event.reply("⚠️ حداکثر %d تبلیغ می‌توانی داشته باشی؛ از پنل یکی را حذف کن." % store.MAX_ADS)
        elif status == "exists":
            await event.reply("♻️ این تبلیغ از قبل ثبت شده بود؛ نامش بروزرسانی شد.")
        else:
            await event.reply("✅ تبلیغ شماره %d ثبت شد. مدیریت: پنل ← تبلیغ‌ها" % ad["id"])
    elif t in ("توقف", "/stop") and st.running:
        engine.stop()
        await event.reply("⏹ ارسال در حال توقف است.")


@user.on(events.NewMessage(func=lambda e: e.is_private and e.out and e.chat_id not in (ctx.owner, ctx.me)))
async def userbot_pv_trigger(event):
    """Owner typed the trigger word to someone else's PV (from their own account).
    Open the panel right there, same as Saved Messages, but never touch the
    other person's messages or send anything besides the panel itself."""
    t = (event.raw_text or "").strip()
    if is_trigger(t):
        await open_panel_in_chat(event)
