"""Guerre de clans : rappels de decks, rapport quotidien et récapitulatif de fin de guerre."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from cr_api import ClashAPIError, NotFound
from embeds import build_daily_report_embed, build_decks_embed, build_war_recap
from war_utils import decks_remaining

log = logging.getLogger("war")


def _key(race: dict) -> tuple[int, int]:
    return race.get("seasonId", 0), race.get("sectionIndex", 0)


class War(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.reminders.start()
        self.daily.start()
        self.recap.start()

    async def cog_unload(self) -> None:
        self.reminders.cancel()
        self.daily.cancel()
        self.recap.cancel()

    async def _channel(self, channel_id: int | None):
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

    # ------------------------------------------------------------------ rappels
    @tasks.loop(seconds=60)
    async def reminders(self) -> None:
        try:
            await self._reminders()
        except ClashAPIError as exc:
            log.warning("Rappel ignoré (API) : %s", exc)
        except Exception:
            log.exception("Erreur dans les rappels de guerre")

    @reminders.before_loop
    async def _before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    async def _reminders(self) -> None:
        cfg, db = self.bot.cfg, self.bot.db
        if not cfg.ready:
            return
        now = datetime.now(ZoneInfo(cfg.timezone))
        for t in cfg.reminder_times:
            target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            late = (now - target).total_seconds()
            day, slot = now.date().isoformat(), t.strftime("%H:%M")
            if 0 <= late < 900 and not await db.reminder_sent(day, slot):
                await db.mark_reminder(day, slot)  # marqué avant l'envoi : pas de doublon
                await self.send_reminder()

    async def send_reminder(self) -> None:
        cfg, db = self.bot.cfg, self.bot.db
        _race, war_day, rows = await decks_remaining(self.bot.api, cfg.clan_tag)
        if not war_day or not rows:
            return
        mentions = []
        for _name, tag, _used in rows:
            player = await db.get_player(tag)
            if player and player.get("discord_id"):
                mentions.append(f"<@{player['discord_id']}>")
        embed = build_decks_embed(rows, "⏰ Rappel guerre : decks à jouer aujourd'hui")
        channel = await self._channel(cfg.war_channel_id)
        if channel is None:
            return
        await channel.send(
            content=" ".join(mentions) or None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    # ------------------------------------------------------------------ rapport quotidien
    @tasks.loop(seconds=60)
    async def daily(self) -> None:
        try:
            await self._daily()
        except ClashAPIError as exc:
            log.warning("Rapport quotidien ignoré (API) : %s", exc)
        except Exception:
            log.exception("Erreur dans le rapport quotidien")

    @daily.before_loop
    async def _before_daily(self) -> None:
        await self.bot.wait_until_ready()

    async def _daily(self) -> None:
        cfg, db = self.bot.cfg, self.bot.db
        if not cfg.ready or not cfg.daily_report_enabled:
            return
        now = datetime.now(ZoneInfo(cfg.timezone))
        t = cfg.daily_report_time
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        late = (now - target).total_seconds()
        day, slot = now.date().isoformat(), "quotidien"
        if 0 <= late < 900 and not await db.reminder_sent(day, slot):
            await db.mark_reminder(day, slot)  # marqué avant l'envoi : pas de doublon
            await self.send_daily_report()

    async def send_daily_report(self) -> None:
        cfg = self.bot.cfg
        try:
            race, war_day, rows = await decks_remaining(self.bot.api, cfg.clan_tag)
        except NotFound:
            race, war_day, rows = None, False, []
        embed = build_daily_report_embed(race, cfg.clan_tag, rows if war_day else [])
        channel = await self._channel(cfg.war_channel_id)
        if channel is not None:
            await channel.send(embed=embed)

    # ------------------------------------------------------------------ récap de fin de guerre
    @tasks.loop(minutes=10)
    async def recap(self) -> None:
        try:
            await self._recap()
        except ClashAPIError as exc:
            log.warning("Récap ignoré (API) : %s", exc)
        except Exception:
            log.exception("Erreur dans le récap de guerre")

    @recap.before_loop
    async def _before_recap(self) -> None:
        await self.bot.wait_until_ready()

    async def _recap(self) -> None:
        cfg, db = self.bot.cfg, self.bot.db
        if not cfg.ready:
            return
        races = sorted(await self.bot.api.river_race_log(cfg.clan_tag, limit=5, ttl=0), key=_key)
        if not races:
            return
        last = await db.get_meta("last_war")
        if last is None:  # première exécution : on mémorise sans poster
            await db.set_meta("last_war", "%d:%d" % _key(races[-1]))
            return
        last_key = tuple(int(x) for x in last.split(":"))
        for race in races:
            if _key(race) > last_key:
                embed = build_war_recap(race, cfg.clan_tag)
                channel = await self._channel(cfg.war_channel_id)
                if embed and channel:
                    await channel.send(embed=embed)
                await db.set_meta("last_war", "%d:%d" % _key(race))


async def setup(bot) -> None:
    await bot.add_cog(War(bot))
