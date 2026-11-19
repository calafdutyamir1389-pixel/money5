"""Target parsing, resolving and group listing."""
import asyncio
import re

from telethon import errors
from telethon.tl import types as tl

from . import config, store
from .core import user
from .state import st

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_PREFIX = re.compile(r"^(https?://)?(www\.)?(t|telegram)\.me/", re.I)


def norm_target(s):
    """@username / t.me link / numeric id -> '@username' or int. None if invalid."""
    s = s.split("#", 1)[0].strip()
    if not s:
        return None
    s = _PREFIX.sub("", s).split("?", 1)[0].strip("/")
    if s.lstrip("-").isdigit():
        return int(s)
    parts = s.split("/")
    if parts[0] == "c" and len(parts) > 1 and parts[1].isdigit():
        return int("-100" + parts[1])
    if parts[0] == "s" and len(parts) > 1:
        parts = parts[1:]
    name = parts[0].lstrip("@")
    if not _USERNAME.match(name):
        return None
    return "@" + name


def describe(entity):
    if isinstance(entity, tl.User):
        name = " ".join(x for x in (entity.first_name, entity.last_name) if x)
        return name or entity.username or str(entity.id), "user"
    title = getattr(entity, "title", None) or str(getattr(entity, "id", ""))
    if isinstance(entity, tl.Channel) and entity.broadcast:
        return title, "channel"
    return title, "group"


async def add_many(lines):
    """Resolve and add targets. Returns dict(added, dup, bad, flood)."""
    res = {"added": 0, "dup": 0, "bad": [], "flood": 0}
    lines = [l for l in lines if l.strip()][: config.MAX_IMPORT]
    for line in lines:
        t = norm_target(line)
        if t is None:
            res["bad"].append(line.strip())
            continue
        if t in st_targets():
            res["dup"] += 1
            continue
        try:
            entity = await user.get_entity(t)
        except errors.FloodWaitError as e:
            res["flood"] = e.seconds
            res["bad"].append(line.strip())
            break
        except Exception:
            res["bad"].append(line.strip())
            continue
        title, kind = describe(entity)
        store.add_target(t, title, kind)
        res["added"] += 1
        await asyncio.sleep(0.3)
    store.save()
    return res


def st_targets():
    return store.data["targets"]


async def load_groups():
    groups = []
    async for d in user.iter_dialogs():
        if d.is_group:
            groups.append({"id": d.id, "title": d.name or str(d.id)})
    st.groups = groups
    return groups
