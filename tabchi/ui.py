"""Panel screens: every screen is built here as (text, buttons)."""
import math
import time
from collections import Counter, namedtuple

from telethon import Button
from telethon.tl import types as tl

from . import config, store
from .fmt import (RULE, ago, bar, clip, dur, esc, eta, fa, hhmm, percent, tz_label)
from .state import st

data = store.data
Screen = namedtuple("Screen", "text buttons name")

PROBLEM_FAILS = 2
KIND_ICON = {"user": "👤", "channel": "📢", "group": "👥"}
MODES = {
    "single": ("🎯 تکی", "فقط اولین تبلیغ فعال ارسال می‌شود"),
    "rotate": ("🔄 چرخشی", "تبلیغ‌های فعال به‌صورت نوبتی ارسال می‌شوند"),
    "random": ("🎲 تصادفی", "برای هر هدف یک تبلیغ فعال تصادفی ارسال می‌شود"),
}
REASONS = {
    "done": "✅ ارسال با موفقیت تمام شد",
    "stopped": "⏹ ارسال متوقف شد",
    "peerflood": "🚫 ارسال به‌دلیل محدودیت تلگرام (PeerFlood) متوقف شد",
    "no_ad": "❌ تبلیغ فعالی پیدا نشد",
    "error": "❌ ارسال با خطا متوقف شد",
}


# ---------------------------------------------------------------- building blocks
_STYLE_OK = None  # None = untested, True = works, False = disabled


def _style_cls():
    """Find the KeyboardButtonStyle TL class (Telethon builds differ on where it lives)."""
    cls = getattr(tl, "KeyboardButtonStyle", None)
    if cls is None:
        try:
            from telethon.tl.alltlobjects import tlobjects
            for c in tlobjects.values():
                if getattr(c, "__name__", "") == "KeyboardButtonStyle":
                    return c
        except Exception:
            pass
    return cls


def cbtn(text, cb, style=None):
    """Inline callback button.

    Built with Telethon's own Button.inline so it always matches the installed
    Telegram layer (layer 229 / Telethon 1.45 reshaped the button classes).
    style: 'primary' (blue) | 'success' (green) | 'danger' (red). Colors are
    best-effort: if this Telethon can't carry them they're skipped."""
    global _STYLE_OK
    b = Button.inline(text, cb)
    if style and config.COLOR_BUTTONS and _STYLE_OK is not False:
        btn = getattr(b, "button", b)
        try:
            cls = _style_cls()
            if cls is None or not hasattr(btn, "style"):
                raise AttributeError("this Telethon has no button style support")
            btn.style = cls(**{"bg_" + style: True})
            bytes(btn)  # must serialize
            _STYLE_OK = True
        except Exception:
            _STYLE_OK = False
            try:
                btn.style = None
            except Exception:
                pass
    return b


def strip_styles(buttons):
    """Remove colors from already-built buttons. True if any color was removed."""
    global _STYLE_OK
    found = False
    for row in buttons:
        for b in row:
            btn = getattr(b, "button", b)
            if getattr(btn, "style", None) is not None:
                found = True
                try:
                    btn.style = None
                except Exception:
                    pass
    if found:
        _STYLE_OK = False
    return found


def colors_supported():
    """True if this Telethon build can send colored buttons."""
    try:
        btn = getattr(Button.inline("x", "x"), "button", None)
        return _style_cls() is not None and btn is not None and hasattr(btn, "style")
    except Exception:
        return False


def markup(buttons):
    """Kept for compatibility: Telethon builds the markup from the rows itself."""
    return buttons


def back(to="main", label="🔙 بازگشت"):
    return [cbtn(label, "go:" + to, "primary")]


def head(title):
    return ["<b>%s</b>" % title, RULE]


def stepper(label, key):
    return [cbtn("➖", "set:%s:-1" % key, "primary"),
            cbtn(label, "noop"),
            cbtn("➕", "set:%s:1" % key, "primary")]


def toggle_row(label, key):
    on = data[key]
    return [cbtn("%s %s: %s" % ("🟢" if on else "⚪️", label, "روشن" if on else "خاموش"),
                 "tog:" + key, "success" if on else "primary")]


def nav_row(page, pages, prefix):
    if pages <= 1:
        return []
    return [[cbtn("◀️", "%s:%d" % (prefix, page - 1) if page > 0 else "noop"),
             cbtn("%s / %s" % (fa(page + 1), fa(pages)), "noop"),
             cbtn("▶️", "%s:%d" % (prefix, page + 1) if page < pages - 1 else "noop")]]


def readiness():
    miss = []
    if not store.enabled_ads():
        miss.append("❌ حداقل یک تبلیغ فعال ثبت کن")
    if not data["targets"]:
        miss.append("❌ هدفی اضافه کن")
    return miss


# ---------------------------------------------------------------- main
def status_line():
    if st.active and not st.running:
        return "🟠 در حال توقف…"
    if st.running and st.paused:
        return "⏸ مکث"
    if st.running:
        return "🟢 در حال ارسال"
    return "🔴 متوقف"


def main_screen():
    d = data
    lines = head(esc(d["panel_title"]))
    lines.append("📡 <b>وضعیت:</b> " + status_line())

    if st.active:
        lines.append("%s  ٪%s" % (bar(st.done, st.total), fa(percent(st.done, st.total))))
        lines.append("🔄 دور %s · %s از %s هدف" % (fa(st.round), fa(st.done), fa(st.total)))
        lines.append("✅ موفق: %s   ❌ ناموفق: %s" % (fa(st.sent), fa(st.failed)))
        if st.running:
            avg = (d["delay_min"] + d["delay_max"]) / 2
            lines.append("⏳ زمان باقی‌مانده: " + eta(max(st.total - st.done, 0) * avg))
        if st.current and st.running and not st.paused:
            lines.append("🎯 هدف فعلی: " + esc(clip(st.current)))
        if st.waiting:
            lines.append("💤 " + esc(st.waiting))
    else:
        miss = readiness()
        if miss:
            lines.append("⚠️ <b>قبل از شروع:</b>")
            lines += miss
        elif d["last_run"]:
            lr = d["last_run"]
            lines.append("🕘 آخرین اجرا (%s): ✅ %s · ❌ %s" % (
                ago(lr.get("end", 0)), fa(lr.get("sent", 0)), fa(lr.get("failed", 0))))

    lines.append(RULE)
    on, total = len(store.enabled_ads()), len(d["ads"])
    lines.append("📨 تبلیغ‌ها: %s فعال از %s · %s" % (fa(on), fa(total), MODES[d["ad_mode"]][0]))
    lines.append("🎯 اهداف: %s" % fa(len(d["targets"])))
    lines.append("⏱ فاصله: %s تا %s ثانیه" % (fa(d["delay_min"]), fa(d["delay_max"])))
    lines.append("🔁 تکرار: %s · 🔀 ترتیب: %s" % (
        "روشن" if d["loop"] else "خاموش", "تصادفی" if d["shuffle"] else "ثابت"))
    today = store.day_sent()
    lines.append("📅 امروز: %s ارسال%s" % (
        fa(today), " از %s" % fa(d["daily_limit"]) if d["daily_limit"] else ""))
    if d["hours_on"]:
        lines.append("🌙 ساعت فعال: %s تا %s" % (hhmm(d["hours_from"]), hhmm(d["hours_to"])))

    if st.active and not st.running:
        row1 = [cbtn("⏳ در حال توقف…", "noop")]
    elif st.running:
        row1 = [cbtn("▶️ ادامه", "run:resume", "success") if st.paused
                else cbtn("⏸ مکث", "run:pause", "primary"),
                cbtn("⏹ توقف", "run:stop", "danger")]
    else:
        row1 = [cbtn("▶️ شروع ارسال", "run:start", "success")]

    buttons = [
        row1,
        [cbtn("📨 تبلیغ‌ها", "go:ads", "primary"), cbtn("🎯 اهداف", "go:targets", "primary")],
        [cbtn("⏱ زمان‌بندی", "go:timing", "primary"), cbtn("🛡 ایمنی و هوشمند", "go:safety", "primary")],
        [cbtn("📊 آمار و گزارش", "go:stats", "primary"), cbtn("🧰 ابزارها", "go:tools", "primary")],
        [cbtn("🎨 شخصی‌سازی", "go:custom", "primary"), cbtn("❓ راهنما", "go:help", "primary")],
        [cbtn("🔄 بروزرسانی", "go:main", "success")],
    ]
    lines.append(RULE)
    lines.append("🤖 " + esc(config.CREDIT))
    return Screen("\n".join(lines), buttons, "main")


# ---------------------------------------------------------------- ads
def ads_screen():
    ads, mode = data["ads"], data["ad_mode"]
    lines = head("📨 مدیریت تبلیغ‌ها")
    if not ads:
        lines += [
            "هنوز تبلیغی ثبت نکرده‌ای.", "",
            "<b>روش ثبت تبلیغ:</b>",
            "۱️⃣ پیام تبلیغ را در <b>Saved Messages</b> بفرست",
            "۲️⃣ روی آن ریپلای کن و بنویس: <code>تنظیم تبلیغ</code>", "",
            "💎 متن، عکس+کپشن، ایموجی پرمیوم و فرمت‌های تلگرام پشتیبانی می‌شود.",
        ]
    else:
        lines += ["فعال: <b>%s</b> از %s" % (fa(len(store.enabled_ads())), fa(len(ads))),
                  "حالت ارسال: <b>%s</b>" % MODES[mode][0],
                  "<i>%s</i>" % MODES[mode][1], RULE]
        for i, ad in enumerate(ads, 1):
            lines.append("%s. %s %s · ارسال: %s" % (
                fa(i), "✅" if ad["enabled"] else "⬜️", esc(clip(ad["name"], 30)), fa(ad["sent"])))
        lines += ["", "روی هر تبلیغ بزن تا فعال/غیرفعال شود."]
    buttons = []
    for i, ad in enumerate(ads, 1):
        buttons.append([
            cbtn("%s %s. %s" % ("✅" if ad["enabled"] else "⬜️", fa(i), clip(ad["name"], 22)),
                 "ad:tog:%d" % ad["id"], "success" if ad["enabled"] else "primary"),
            cbtn("🗑", "ad:del:%d" % ad["id"], "danger")])
    buttons += [
        [cbtn("🔀 حالت ارسال: " + MODES[mode][0], "ad:mode", "primary")],
        [cbtn("➕ راهنمای افزودن", "ad:how", "success"), cbtn("👁 پیش‌نمایش", "ad:prev", "primary")],
        back(),
    ]
    return Screen("\n".join(lines), buttons, "ads")


def confirm_screen(text, yes, no):
    return Screen("<b>⚠️ تایید</b>\n%s\n%s" % (RULE, text),
                  [[cbtn("✅ بله", yes, "danger"), cbtn("❌ خیر", no, "primary")]], "confirm")


# ---------------------------------------------------------------- targets
def targets_screen():
    ts = data["targets"]
    infos = [store.info_of(t) for t in ts]
    kinds = Counter(i.get("kind", "") for i in infos)
    problem = sum(1 for i in infos if i.get("fails", 0) >= PROBLEM_FAILS)
    lines = head("🎯 مدیریت اهداف")
    lines.append("📌 تعداد کل: <b>%s</b>" % fa(len(ts)))
    parts = ["%s %s" % (KIND_ICON[k], fa(kinds[k])) for k in ("group", "channel", "user") if kinds.get(k)]
    if parts:
        lines.append(" · ".join(parts))
    if problem:
        lines.append("⚠️ مشکل‌دار: %s (چند بار ناموفق پشت‌سرهم)" % fa(problem))
    lines += [RULE, "هدف می‌تواند <code>@username</code>، لینک <code>t.me</code> یا آیدی عددی باشد."]
    buttons = [
        [cbtn("📋 مشاهده و حذف", "go:tlist:0", "primary")],
        [cbtn("➕ افزودن دستی", "ask:addt", "success"), cbtn("👥 انتخاب از گروه‌هایم", "go:groups:0", "success")],
        [cbtn("📤 خروجی لیست", "t:export", "primary"), cbtn("🧹 حذف مشکل‌دارها", "t:purge", "primary")],
        [cbtn("🗑 پاک کردن همه", "t:clearask", "danger")],
        back(),
    ]
    return Screen("\n".join(lines), buttons, "targets")


def targets_list_screen(page=0):
    ts = list(data["targets"])
    pages = max(1, math.ceil(len(ts) / config.PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = ts[page * config.PAGE_SIZE:(page + 1) * config.PAGE_SIZE]
    lines = head("📋 لیست اهداف")
    if ts:
        lines += ["مجموع: %s · صفحه %s از %s" % (fa(len(ts)), fa(page + 1), fa(pages)), "",
                  "برای <b>حذف</b> هر هدف روی آن بزن.", "⚠️ = چند بار ناموفق بوده"]
    else:
        lines.append("لیست خالی است.")
    buttons = []
    for t in chunk:
        inf = store.info_of(t)
        warn = "⚠️ " if inf.get("fails", 0) >= PROBLEM_FAILS else ""
        icon = KIND_ICON.get(inf.get("kind"), "🎯")
        buttons.append([cbtn("🗑 %s%s %s" % (warn, icon, clip(store.title_of(t), 24)),
                             "t:rm:%d:%s" % (page, t), "danger")])
    buttons += nav_row(page, pages, "go:tlist")
    buttons.append(back("targets"))
    return Screen("\n".join(lines), buttons, "tlist")


def groups_screen(page=0):
    gs = st.groups
    pages = max(1, math.ceil(len(gs) / config.PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = gs[page * config.PAGE_SIZE:(page + 1) * config.PAGE_SIZE]
    lines = head("👥 گروه‌های من")
    if gs:
        lines += ["مجموع: %s · صفحه %s از %s" % (fa(len(gs)), fa(page + 1), fa(pages)), "",
                  "✅ = داخل لیست اهداف هست. برای افزودن/حذف روی گروه بزن."]
    else:
        lines.append("گروهی پیدا نشد. با «بازخوانی» دوباره امتحان کن.")
    buttons = []
    for g in chunk:
        sel = g["id"] in data["targets"]
        buttons.append([cbtn("%s %s" % ("✅" if sel else "⬜️", clip(g["title"], 28)),
                             "g:tog:%d:%d" % (page, g["id"]), "success" if sel else "primary")])
    buttons += nav_row(page, pages, "go:groups")
    buttons.append([cbtn("✅ افزودن همه", "g:all:%d" % page, "success"),
                    cbtn("🔄 بازخوانی", "g:reload:%d" % page, "primary")])
    buttons.append(back("targets"))
    return Screen("\n".join(lines), buttons, "groups")


# ---------------------------------------------------------------- settings
def timing_screen():
    d = data
    lines = head("⏱ زمان‌بندی ارسال")
    lines += ["بین هر دو ارسال، به‌صورت تصادفی بین حداقل و حداکثر صبر می‌شود.",
              "فاصله‌ی بیشتر = فشار کمتر روی اکانت."]
    buttons = [
        stepper("حداقل: %s ث" % fa(d["delay_min"]), "delay_min"),
        stepper("حداکثر: %s ث" % fa(d["delay_max"]), "delay_max"),
        [cbtn("✏️ ورود دستی فاصله", "ask:delay", "primary")],
        toggle_row("تکرار خودکار", "loop"),
        stepper("بین دورها: %s دقیقه" % fa(d["loop_gap_min"]), "loop_gap_min"),
        toggle_row("ساعت فعال", "hours_on"),
        stepper("از ساعت %s" % hhmm(d["hours_from"]), "hours_from"),
        stepper("تا ساعت %s" % hhmm(d["hours_to"]), "hours_to"),
        stepper("منطقه‌ی زمانی: %s" % tz_label(d["tz_offset"]), "tz_offset"),
        back(),
    ]
    lines += ["", "🌙 با روشن بودن «ساعت فعال»، ارسال فقط در بازه‌ی انتخابی انجام می‌شود",
              "و بیرون از آن منتظر می‌ماند.",
              "🕒 اکنون: %s" % fa(store.now_local().strftime("%H:%M"))]
    return Screen("\n".join(lines), buttons, "timing")


def safety_screen():
    d = data
    lim = fa(d["daily_limit"]) if d["daily_limit"] else "نامحدود"
    auto = "بعد از %s شکست" % fa(d["auto_remove"]) if d["auto_remove"] else "خاموش"
    lines = head("🛡 ایمنی و هوشمند")
    lines += ["📅 سقف روزانه: <b>%s</b>" % lim,
              "🧹 حذف خودکار اهداف خراب: <b>%s</b>" % auto, "",
              "<i>هیچ تنظیمی تضمین نمی‌کند اکانت محدود نشود؛ فاصله‌ی بیشتر،",
              "سقف روزانه و ارسال فقط به اهداف رضایت‌مند ریسک را کم می‌کند.</i>"]
    buttons = [
        stepper("سقف روزانه: %s" % lim, "daily_limit"),
        stepper("حذف خودکار: %s" % auto, "auto_remove"),
        toggle_row("ترتیب تصادفی اهداف", "shuffle"),
        toggle_row("اعلان پایان ارسال", "notify_finish"),
        toggle_row("اعلان خطا و محدودیت", "notify_error"),
        back(),
    ]
    return Screen("\n".join(lines), buttons, "safety")


# ---------------------------------------------------------------- stats / reports
def stats_screen():
    s = data["stats"]
    total = s["sent"] + s["failed"]
    rate = percent(s["sent"], total)
    ts = data["targets"]
    problems = sorted(((t, store.info_of(t)) for t in ts
                       if store.info_of(t).get("last_error")),
                      key=lambda x: x[1].get("fails", 0), reverse=True)[:5]
    lines = head("📊 آمار و گزارش")
    lines += ["📅 امروز: <b>%s</b> ارسال%s" % (
                  fa(store.day_sent()),
                  " (سقف %s)" % fa(data["daily_limit"]) if data["daily_limit"] else ""),
              "📈 کل ارسال‌ها: ✅ %s · ❌ %s" % (fa(s["sent"]), fa(s["failed"])),
              "🎯 نرخ موفقیت: ٪%s" % fa(rate),
              "🔁 تعداد اجرا: %s" % fa(s["runs"])]
    if problems:
        lines += [RULE, "🚧 <b>اهداف مشکل‌دار:</b>"]
        for t, inf in problems:
            lines.append("• %s — %s" % (esc(clip(store.title_of(t), 20)), esc(inf["last_error"])))
    buttons = [
        [cbtn("📜 گزارش آخرین ارسال", "go:lastrun", "primary")],
        [cbtn("📄 خروجی خطاها", "t:errfile", "primary"), cbtn("♻️ ریست آمار", "tool:statsask", "danger")],
        back(),
    ]
    return Screen("\n".join(lines), buttons, "stats")


def finish_text(last):
    lines = [REASONS.get(last.get("reason"), REASONS["done"]), RULE]
    sent, failed = last.get("sent", 0), last.get("failed", 0)
    lines += ["⏱ مدت: %s" % dur(last.get("end", 0) - last.get("start", 0)),
              "🔄 تعداد دور: %s" % fa(last.get("rounds", 0)),
              "✅ موفق: %s" % fa(sent), "❌ ناموفق: %s" % fa(failed),
              "🎯 نرخ موفقیت: ٪%s" % fa(percent(sent, sent + failed))]
    errs = Counter(r for _, r in last.get("errors", []))
    if errs:
        lines += [RULE, "🚧 <b>دلیل خطاها:</b>"]
        for reason, n in errs.most_common(5):
            lines.append("• %s: %s" % (esc(reason), fa(n)))
    if last.get("removed"):
        lines += [RULE, "🧹 حذف خودکار شد: %s هدف" % fa(len(last["removed"]))]
    if last.get("reason") == "peerflood":
        lines += ["", "چند روز صبر کن و به @SpamBot پیام بده؛ بعد سرعت و تعداد را کم کن."]
    return "\n".join(lines)


def lastrun_screen():
    last = data["last_run"]
    text = ("<b>📜 گزارش آخرین ارسال</b>\n%s\n%s" % (RULE, finish_text(last))
            if last else "<b>📜 گزارش آخرین ارسال</b>\n%s\nهنوز ارسالی انجام نشده." % RULE)
    return Screen(text, [back("stats")], "lastrun")


# ---------------------------------------------------------------- tools / custom / help / system
def tools_screen():
    lines = head("🧰 ابزارها")
    lines += ["🧪 <b>تست هدف:</b> تبلیغ را فقط به یک هدف می‌فرستد",
              "💾 <b>بکاپ / ریستور:</b> همه‌ی تنظیمات در یک فایل JSON",
              "🖥 <b>سیستم:</b> اطلاعات اجرا و وضعیت"]
    buttons = [
        [cbtn("🧪 تست روی یک هدف", "ask:test", "primary")],
        [cbtn("💾 بکاپ", "tool:backup", "success"), cbtn("♻️ ریستور", "ask:restore", "danger")],
        [cbtn("🖥 اطلاعات سیستم", "go:sys", "primary")],
        back(),
    ]
    return Screen("\n".join(lines), buttons, "tools")


def custom_screen():
    lines = head("🎨 شخصی‌سازی")
    lines += ["⌨️ کلمه‌ی باز کردن پنل: <code>%s</code>" % esc(data["trigger"]),
              "<i>این کلمه را در Saved Messages یا پیوی ربات بنویس تا پنل باز شود.</i>", "",
              "🖋 عنوان پنل: <b>%s</b>" % esc(data["panel_title"]),
              "💬 متن بازشدن: <b>%s</b>" % esc(data["open_text"])]
    buttons = [[cbtn("⌨️ تغییر کلمه‌ی باز کردن پنل", "ask:trigger", "success")],
               [cbtn("🖋 تغییر عنوان پنل", "ask:title", "primary")],
               [cbtn("💬 تغییر متن بازشدن", "ask:opentext", "primary")],
               back()]
    return Screen("\n".join(lines), buttons, "custom")


START_TIME = time.time()


def sys_screen(owner, bot_username):
    import os
    try:
        import telethon
        tv = telethon.__version__
    except Exception:
        tv = "?"
    size = 0
    try:
        size = os.path.getsize(config.DATA_PATH)
    except OSError:
        pass
    lines = head("🖥 اطلاعات سیستم")
    lines += ["🏷 نسخه: %s" % fa(config.VERSION),
              "⏳ مدت فعالیت: %s" % dur(time.time() - START_TIME),
              "👤 مالک: <code>%s</code>" % owner,
              "🤖 ربات: @%s" % bot_username,
              "📚 Telethon: %s" % tv,
              "💾 فایل داده: <code>%s</code> (%s بایت)" % (esc(config.DATA_PATH), fa(size))]
    return Screen("\n".join(lines), [back("tools")], "sys")


HELP = {
    "start": ("🚀 شروع سریع", [
        "۱. پیام تبلیغ را در Saved Messages بفرست.",
        "۲. روی آن ریپلای کن و بنویس: <code>تنظیم تبلیغ</code>",
        "۳. از «🎯 اهداف» گروه‌ها و کانال‌ها را اضافه کن.",
        "۴. از «⏱ زمان‌بندی» فاصله‌ی ارسال را تنظیم کن.",
        "۵. «▶️ شروع ارسال» را بزن؛ پیشرفت روی همین پنل زنده نمایش داده می‌شود."]),
    "ads": ("📨 تبلیغ‌ها", [
        "تا ۱۰ تبلیغ می‌توانی ثبت کنی و هرکدام را فعال/غیرفعال کنی.",
        "• <b>تکی</b>: فقط اولین تبلیغ فعال",
        "• <b>چرخشی</b>: نوبتی بین تبلیغ‌های فعال",
        "• <b>تصادفی</b>: هر بار یکی از فعال‌ها",
        "اگر پیام تبلیغ را از Saved Messages پاک کنی، از ارسال کنار گذاشته می‌شود."]),
    "targets": ("🎯 اهداف", [
        "• افزودن دستی: هر خط یک هدف، یا فایل txt.",
        "• انتخاب از گروه‌هایم: تیک‌دار کردن گروه‌ها با یک ضربه.",
        "• ⚠️ یعنی چند بار پشت‌سرهم ناموفق بوده.",
        "• «حذف مشکل‌دارها» و «حذف خودکار» لیست را تمیز نگه می‌دارند.",
        "لینک دعوت خصوصی (<code>t.me/+…</code>) پشتیبانی نمی‌شود."]),
    "safety": ("🛡 نکات ایمنی", [
        "• فقط به گروه‌ها و افرادی بفرست که با دریافت پیام مشکلی ندارند.",
        "• فاصله‌ی ۳۰ ثانیه یا بیشتر و سقف روزانه را فعال نگه دار.",
        "• هیچ تنظیمی بن‌نشدن اکانت را تضمین نمی‌کند.",
        "• مسئولیت استفاده با خودت است."]),
    "cmds": ("⌨️ دستورات", [
        "<b>Saved Messages</b>",
        "• <code>پنل</code> / <code>panel</code>: باز کردن پنل",
        "• <code>تنظیم تبلیغ</code> (ریپلای): ثبت تبلیغ",
        "• <code>توقف</code>: توقف فوری ارسال", "",
        "<b>پیوی ربات</b>",
        "• /start · /panel · /status · /stop · /cancel · /help"]),
}


def _cmds_body():
    return ["<b>Saved Messages و پیوی ربات</b>",
            "• <code>%s</code>: باز کردن پنل" % esc(data["trigger"]),
            "• کلمه را از «🎨 شخصی‌سازی» می‌توانی عوض کنی", "",
            "<b>Saved Messages</b>",
            "• <code>تنظیم تبلیغ</code> (ریپلای): ثبت تبلیغ",
            "• <code>توقف</code>: توقف فوری ارسال", "",
            "<b>پیوی ربات</b>",
            "• /start · /panel · /status · /stop · /cancel · /help"]


def help_screen(topic=None):
    if topic in HELP:
        title, body = HELP[topic]
        if topic == "cmds":
            body = _cmds_body()
        return Screen("\n".join(head(title) + body), [back("help", "🔙 فهرست راهنما")], "help")
    lines = head("❓ راهنما") + ["یک موضوع را انتخاب کن:"]
    buttons = [[cbtn("🚀 شروع سریع", "go:help:start", "success"), cbtn("📨 تبلیغ‌ها", "go:help:ads", "primary")],
               [cbtn("🎯 اهداف", "go:help:targets", "primary"), cbtn("🛡 نکات ایمنی", "go:help:safety", "primary")],
               [cbtn("⌨️ دستورات", "go:help:cmds", "primary")],
               back()]
    return Screen("\n".join(lines), buttons, "help")


SCREENS = {
    "main": main_screen, "ads": ads_screen, "targets": targets_screen,
    "timing": timing_screen, "safety": safety_screen, "stats": stats_screen,
    "tools": tools_screen, "custom": custom_screen, "help": help_screen,
    "lastrun": lastrun_screen, "tlist": targets_list_screen, "groups": groups_screen,
}
