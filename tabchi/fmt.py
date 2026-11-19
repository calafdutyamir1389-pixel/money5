"""Formatting helpers shared by the UI and reports."""
import html
import time

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

RULE = "┈" * 16


def fa(x):
    return str(x).translate(_FA)


def en(s):
    return str(s).translate(_EN)


_NORM = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "\u200c": " ",
                       "\u200f": "", "\u200e": "", "\u202a": "", "\u202b": "", "\u202c": ""})


def norm_text(s):
    """Normalize Arabic/Persian letter variants, ZWNJ, spaces and case for matching."""
    return " ".join(str(s).translate(_NORM).lower().split())


def esc(s):
    return html.escape(str(s), quote=False)


def clip(s, n=26):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def percent(done, total):
    if total <= 0:
        return 0
    return int(min(max(done / total, 0), 1) * 100)


def bar(done, total, width=12):
    filled = round(percent(done, total) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def dur(sec):
    sec = int(max(sec, 0))
    if sec < 60:
        return "%s ثانیه" % fa(sec)
    m, s = divmod(sec, 60)
    if m < 60:
        return "%s دقیقه" % fa(m) + (" و %s ثانیه" % fa(s) if s else "")
    h, m = divmod(m, 60)
    return "%s ساعت" % fa(h) + (" و %s دقیقه" % fa(m) if m else "")


def eta(sec):
    if sec < 60:
        return "کمتر از یک دقیقه"
    m = round(sec / 60)
    if m < 60:
        return "حدود %s دقیقه" % fa(m)
    h, m = divmod(m, 60)
    return "حدود %s ساعت" % fa(h) + (" و %s دقیقه" % fa(m) if m else "")


def ago(ts):
    diff = time.time() - ts
    if diff < 60:
        return "لحظاتی پیش"
    if diff < 3600:
        return "%s دقیقه پیش" % fa(int(diff // 60))
    if diff < 86400:
        return "%s ساعت پیش" % fa(int(diff // 3600))
    return "%s روز پیش" % fa(int(diff // 86400))


def hhmm(hour):
    return fa("%02d:00" % hour)


def tz_label(minutes):
    sign = "+" if minutes >= 0 else "-"
    h, m = divmod(abs(minutes), 60)
    return "UTC%s%s" % (sign, fa(h)) + (":%s" % fa("%02d" % m) if m else "")
