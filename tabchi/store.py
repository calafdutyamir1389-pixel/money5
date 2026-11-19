"""Persistent settings: load / migrate / atomic save plus small data helpers."""
import copy
import json
import os
from datetime import datetime, timedelta, timezone

from . import config

MAX_ADS = 10
MODES = ("single", "rotate", "random")

DEFAULTS = {
    "panel_title": "🤖 پنل ربات تبچی سینا",
    "open_text": "🔓 باز کردن پنل تبچی سینا",
    "trigger": "پنل تبچی",   # word that opens the panel (Saved Messages / bot PV)
    "targets": [],
    "target_info": {},   # str(target) -> {title, kind, sent, fails, last_error}
    "ads": [],           # [{id, msg_id, name, enabled, sent}]
    "ad_seq": 0,
    "ad_mode": "single",
    "delay_min": 30,
    "delay_max": 60,
    "loop": False,
    "loop_gap_min": 60,
    "shuffle": False,
    "daily_limit": 0,
    "hours_on": False,
    "hours_from": 9,
    "hours_to": 23,
    "tz_offset": 210,
    "auto_remove": 0,
    "notify_finish": True,
    "notify_error": True,
    "stats": {"sent": 0, "failed": 0, "runs": 0, "day": "", "day_sent": 0},
    "last_run": None,
}

# key -> (step, min, max)
STEPS = {
    "delay_min": (5, 1, 3600),
    "delay_max": (5, 1, 7200),
    "loop_gap_min": (10, 5, 1440),
    "daily_limit": (10, 0, 2000),
    "hours_from": (1, 0, 23),
    "hours_to": (1, 0, 23),
    "tz_offset": (30, -720, 840),
    "auto_remove": (1, 0, 20),
}
WRAP = ("hours_from", "hours_to")
TOGGLES = ("loop", "shuffle", "hours_on", "notify_finish", "notify_error")


def _ok(key, value):
    default = DEFAULTS[key]
    if default is None:
        return value is None or isinstance(value, dict)
    if isinstance(value, bool) != isinstance(default, bool):
        return False
    return isinstance(value, type(default))


def _migrate(raw):
    """Convert the old single-ad format (ad_msg_id) to the ads list."""
    legacy = raw.get("ad_msg_id")
    if isinstance(legacy, int) and not raw.get("ads"):
        raw["ads"] = [{"id": 1, "msg_id": legacy, "name": "تبلیغ قبلی",
                       "enabled": True, "sent": 0}]
        raw["ad_seq"] = 1


def _sanitize(d):
    stats = copy.deepcopy(DEFAULTS["stats"])
    stats.update({k: v for k, v in d["stats"].items() if k in stats})
    d["stats"] = stats
    d["trigger"] = d["trigger"].strip() or DEFAULTS["trigger"]
    d["delay_min"] = max(1, d["delay_min"])
    d["delay_max"] = max(d["delay_min"], d["delay_max"])
    if d["ad_mode"] not in MODES:
        d["ad_mode"] = "single"
    d["hours_from"] %= 24
    d["hours_to"] %= 24
    d["targets"] = [t for t in d["targets"]
                    if isinstance(t, (int, str)) and not isinstance(t, bool)]
    ads = []
    for a in d["ads"]:
        if isinstance(a, dict) and isinstance(a.get("msg_id"), int) and isinstance(a.get("id"), int):
            ads.append({"id": a["id"], "msg_id": a["msg_id"],
                        "name": str(a.get("name") or "تبلیغ"),
                        "enabled": bool(a.get("enabled", True)),
                        "sent": int(a.get("sent", 0) or 0)})
    d["ads"] = ads
    d["ad_seq"] = max([d["ad_seq"]] + [a["id"] for a in ads])
    d["target_info"] = {k: v for k, v in d["target_info"].items() if isinstance(v, dict)}


def _build(raw):
    raw = dict(raw)
    _migrate(raw)
    d = copy.deepcopy(DEFAULTS)
    recognized = 0
    for k in DEFAULTS:
        if k in raw and _ok(k, raw[k]):
            d[k] = copy.deepcopy(raw[k])
            recognized += 1
    _sanitize(d)
    return d, recognized


def load():
    try:
        with open(config.DATA_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return _build(raw)[0]


data = load()


def save():
    path = config.DATA_PATH
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def restore(raw):
    """Replace settings in place from a backup dict. Returns recognized key count."""
    if not isinstance(raw, dict):
        raise ValueError("bad backup")
    d, n = _build(raw)
    if n == 0:
        raise ValueError("empty backup")
    data.clear()
    data.update(d)
    save()
    return n


# ---------------------------------------------------------------- time / counters
def now_local():
    return datetime.now(timezone.utc) + timedelta(minutes=data["tz_offset"])


def today_key():
    return now_local().strftime("%Y-%m-%d")


def day_sent():
    s = data["stats"]
    return s["day_sent"] if s.get("day") == today_key() else 0


def bump_sent():
    s = data["stats"]
    t = today_key()
    if s.get("day") != t:
        s["day"], s["day_sent"] = t, 0
    s["day_sent"] += 1
    s["sent"] += 1


def reset_stats():
    data["stats"] = copy.deepcopy(DEFAULTS["stats"])
    data["last_run"] = None
    for inf in data["target_info"].values():
        inf.update(sent=0, fails=0, last_error="")
    for ad in data["ads"]:
        ad["sent"] = 0
    save()


# ---------------------------------------------------------------- settings
def step(key, sign):
    s, lo, hi = STEPS[key]
    v = data[key] + sign * s
    v = v % 24 if key in WRAP else max(lo, min(hi, v))
    data[key] = v
    if key == "delay_min" and data["delay_min"] > data["delay_max"]:
        data["delay_max"] = data["delay_min"]
    if key == "delay_max" and data["delay_max"] < data["delay_min"]:
        data["delay_min"] = data["delay_max"]
    save()


def toggle(key):
    data[key] = not data[key]
    save()


def cycle_mode():
    i = MODES.index(data["ad_mode"])
    data["ad_mode"] = MODES[(i + 1) % len(MODES)]
    save()


# ---------------------------------------------------------------- targets
def info_of(t):
    return data["target_info"].get(str(t), {})


def title_of(t):
    return info_of(t).get("title") or str(t)


def find_target(key):
    for t in data["targets"]:
        if str(t) == key:
            return t
    return None


def add_target(t, title=None, kind=""):
    if t in data["targets"]:
        return False
    data["targets"].append(t)
    data["target_info"][str(t)] = {"title": title or str(t), "kind": kind,
                                   "sent": 0, "fails": 0, "last_error": ""}
    return True


def remove_target(t):
    if t in data["targets"]:
        data["targets"].remove(t)
    data["target_info"].pop(str(t), None)


def clear_targets():
    data["targets"] = []
    data["target_info"] = {}
    save()


# ---------------------------------------------------------------- ads
def get_ad(ad_id):
    for a in data["ads"]:
        if a["id"] == ad_id:
            return a
    return None


def enabled_ads():
    return [a for a in data["ads"] if a["enabled"]]


def add_ad(msg_id, name):
    """Returns (ad, status) where status is 'added' | 'exists' | 'full'."""
    for a in data["ads"]:
        if a["msg_id"] == msg_id:
            a["name"] = name
            save()
            return a, "exists"
    if len(data["ads"]) >= MAX_ADS:
        return None, "full"
    data["ad_seq"] += 1
    ad = {"id": data["ad_seq"], "msg_id": msg_id, "name": name, "enabled": True, "sent": 0}
    data["ads"].append(ad)
    save()
    return ad, "added"


def remove_ad(ad_id):
    data["ads"] = [a for a in data["ads"] if a["id"] != ad_id]
    save()
