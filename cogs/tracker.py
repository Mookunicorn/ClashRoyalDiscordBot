"""Surveille le clan : arrivées, départs, promotions et réglages (ouvert/fermé, trophées requis).

L'API Clash Royale ne propose pas de notifications « push » : le bot interroge le clan à
intervalle court (10 s par défaut, réglable via /configurer) et publie dès qu'un
changement apparaît. Les arrivées sont annoncées immédiatement, l'historique détaillé
(5 derniers clans, guerres) vient compléter le message quelques secondes après.
"""
from __future__ import annotations

import json
import logging

import discord
from discord.ext import commands, tasks

from cr_api import ClashAPIError
from embeds import (
    ROLE_RANK,
    build_clan_settings_embed,
    build_leave_embed,
    build_player_embed,
    build_role_embed,
    clan_snapshot,
)
from history import build_history

log = logging.getLogger("tracker")


class Tracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._startup_synced = False
        self._errors = 0

    async def cog_load(self) -> None:
        self.poll.change_interval(seconds=self.bot.cfg.poll_interval)
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    @tasks.loop(seconds=10)
    async def poll(self) -> None:
        try:
            await self.run_once()
            if self._errors:                      # retour à la normale : on reprend le rythme configuré
                self._errors = 0
                self.poll.change_interval(seconds=self.bot.cfg.poll_interval)
        except ClashAPIError as exc:
            # API indisponible ou limite de débit (429) : on espace les requêtes (max 2 min)
            self._errors += 1
            delay = min(self.bot.cfg.poll_interval * 2 ** min(self._errors, 5), 120)
            self.poll.change_interval(seconds=delay)
            log.warning("Cycle ignoré (API) : %s — prochain essai dans %ds", exc, delay)
        except Exception:
            log.exception("Erreur inattendue dans le suivi du clan")

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_settings_changed(self, key: str) -> None:
        if key == "intervalle":
            self.poll.change_interval(seconds=self.bot.cfg.poll_interval)
        elif key == "clan":
            self._startup_synced = False   # nouvelle synchro complète dès la prochaine surveillance

    # ------------------------------------------------------------------ envoi
    async def send(self, channel_id: int | None, **kwargs) -> discord.Message | None:
        if not channel_id:
            log.warning("Aucun salon configuré pour cette notification (/configurer)")
            return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            return await channel.send(**kwargs)
        except discord.HTTPException:
            log.exception("Envoi impossible dans le salon %s", channel_id)
            return None

    # ------------------------------------------------------------------ cycle principal
    async def run_once(self) -> None:
        bot, db, cfg = self.bot, self.bot.db, self.bot.cfg
        if not cfg.ready:          # clé API ou clan pas encore renseignés (/configurer)
            return
        clan = await bot.api.clan(cfg.clan_tag)
        members = clan.get("memberList") or []
        if not members:
            log.warning("Liste de membres vide : cycle ignoré")
            return
        current = {m["tag"]: m for m in members}
        bot.clan_info = clan

        open_tags = await db.open_tags()
        if not await db.get_meta("bootstrapped"):
            # Première exécution : on enregistre l'existant sans notifier.
            bot.members = current
            for m in members:
                await db.upsert_player(m["tag"], m["name"], m.get("role"))
                await db.open_stint(m["tag"], initial=True)
            await self.check_clan_settings(clan, silent=True)
            await db.set_meta("bootstrapped", "1")
            log.info("Initialisation : %d membres enregistrés", len(members))
            await self._startup_sync()
            return

        await self.check_clan_settings(clan)

        # Garde-fou : une liste anormalement courte ne doit pas générer 30 « départs ».
        if len(open_tags) >= 6 and len(current) < len(open_tags) * 0.5:
            log.warning("Chute suspecte de l'effectif (%d -> %d) : cycle ignoré", len(open_tags), len(current))
            return
        bot.members = current  # seulement une fois la liste jugée fiable (sert aux rôles Discord)

        joined = [t for t in current if t not in open_tags]
        left = [t for t in open_tags if t not in current]

        # Départs d'abord (message immédiat), puis arrivées (message immédiat + complément)
        for tag in left:
            try:
                await self.handle_leave(tag)
            except Exception:
                log.exception("Échec du traitement du départ de %s", tag)
        for tag in joined:
            try:
                await self.handle_join(current[tag])
            except Exception:
                log.exception("Échec du traitement de l'arrivée de %s", tag)
        for tag, m in current.items():
            if tag not in joined:
                await self.check_role(tag, m)
        await self._startup_sync()

    async def _startup_sync(self) -> None:
        """Une seule synchro complète par démarrage (rôles configurés hors-ligne, pseudos modifiés…)."""
        if not self._startup_synced:
            self._startup_synced = True
            stats = await self.bot.sync.sync_all()
            log.info("Synchro Discord au démarrage : %s", stats)

    # ------------------------------------------------------------------ événements
    async def handle_join(self, member: dict) -> None:
        bot, db, cfg = self.bot, self.bot.db, self.bot.cfg
        tag = member["tag"]
        await db.upsert_player(tag, member["name"], member.get("role"))
        await db.open_stint(tag)  # ouvert d'abord : évite tout doublon si l'envoi échoue
        stints = await db.stints(tag)

        # 1. annonce immédiate (données déjà disponibles dans la liste du clan)
        quick = build_player_embed(profile=dict(member), stints=stints, history=None, mode="join", loading=True)
        message = await self.send(cfg.join_channel_id, embed=quick)
        log.info("Arrivée notifiée : %s (%s)", member["name"], tag)

        # 2. pseudo + rôle si le joueur est déjà lié (retour dans le clan)
        await bot.sync.apply_safe(tag)

        # 3. complément : profil complet, 5 derniers clans, moyenne des guerres
        try:
            profile = await bot.api.player(tag)
        except ClashAPIError:
            profile = dict(member)
        history = await build_history(bot.api, db, tag, cfg.clan_tag)
        full = build_player_embed(profile=profile, stints=stints, history=history, mode="join")
        if message is not None:
            try:
                await message.edit(embed=full)
            except discord.HTTPException:
                log.warning("Complément d'historique non publié pour %s", tag)

    async def handle_leave(self, tag: str) -> None:
        db, cfg = self.bot.db, self.bot.cfg
        stint = await db.close_stint(tag)
        embed = build_leave_embed(await db.get_player(tag), tag, stint, len(await db.stints(tag)))
        await self.send(cfg.leave_channel_id, embed=embed)
        log.info("Départ notifié : %s", tag)
        await self.bot.sync.apply_safe(tag)  # retire les rôles de rang

    async def check_role(self, tag: str, member: dict) -> None:
        db, cfg = self.bot.db, self.bot.cfg
        row = await db.get_player(tag)
        old, new = (row or {}).get("role"), member.get("role")
        old_name = (row or {}).get("name")
        if old and new and old != new and old in ROLE_RANK and new in ROLE_RANK:
            await self.send(cfg.log_channel_id, embed=build_role_embed(row, member, old, new))
        await db.upsert_player(tag, member["name"], new)
        if (old and old != new) or (old_name and old_name != member["name"]):
            await self.bot.sync.apply_safe(tag)  # promotion ou changement de pseudo en jeu

    async def check_clan_settings(self, clan: dict, silent: bool = False) -> None:
        """Annonce dans les logs : clan ouvert / sur invitation / fermé, trophées requis, nom…"""
        db, cfg = self.bot.db, self.bot.cfg
        raw = await db.get_meta("clan_settings")
        old = json.loads(raw) if raw else None
        # un champ absent de la réponse ne doit pas écraser la valeur connue
        new = {**(old or {}), **{k: v for k, v in clan_snapshot(clan).items() if v is not None}}
        if new == old:
            return
        await db.set_meta("clan_settings", json.dumps(new))   # enregistré avant l'envoi : jamais de doublon
        if old is None or silent:
            return
        embed = build_clan_settings_embed(old, new, clan)
        if embed is not None:
            await self.send(cfg.log_channel_id, embed=embed)
            log.info("Réglages du clan modifiés : %s", embed.title)


async def setup(bot) -> None:
    await bot.add_cog(Tracker(bot))
