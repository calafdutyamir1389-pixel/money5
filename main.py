import asyncio
import logging

from tabchi import config, handlers
from tabchi.core import bot, ctx, user

logging.basicConfig(level=logging.INFO)
logging.getLogger("telethon").setLevel(logging.WARNING)  # hide "Got difference" spam
log = logging.getLogger("tabchi")


async def self_check():
    """Warn early about the two most common setup mistakes."""
    await asyncio.sleep(3)
    try:
        results = await user.inline_query(ctx.bot_username, "panel")
        if not results:
            raise RuntimeError("no inline results")
        log.info("Inline mode OK.")
    except Exception as e:
        log.warning("Inline mode check failed (%r). Enable it with BotFather /setinline.", e)
        try:
            await bot.send_message(ctx.owner, handlers.NO_INLINE_HINT)
        except Exception:
            log.warning("Cannot message the owner: open @%s and send /start once.", ctx.bot_username)


async def main():
    # Resolve ctx.owner BEFORE the bot client connects: bot.start() can start
    # dispatching incoming messages (e.g. /start) immediately, and if that
    # happens before ctx.owner is set, every handler sees "sender != owner"
    # and silently falls back to the stranger-reply branch.
    await user.start()
    me = await user.get_me()
    ctx.me = me.id
    ctx.owner = config.OWNER_ID or me.id
    await bot.start(bot_token=config.BOT_TOKEN)
    ctx.bot_username = (await bot.get_me()).username
    await user.get_dialogs()  # fill entity cache
    asyncio.create_task(handlers.live_ticker())
    asyncio.create_task(self_check())
    log.info("Tabchi Sina v%s is running. Owner=%s Bot=@%s",
             config.VERSION, ctx.owner, ctx.bot_username)
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())
