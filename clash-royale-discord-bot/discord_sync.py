"""Synchronisation Clash Royale -> Discord : pseudo en jeu + rôle correspondant au rang.

Un joueur est « lié » quand son tag Clash Royale est associé à un compte Discord
(bouton « Lier mon compte », /lier). À chaque changement (arrivée, départ, promotion,
changement de pseudo en jeu) le bot met à jour le pseudo et les rôles.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord

log = logging.getLogger("sync")

ALL_KEY = "_all"  # rôle commun à tous les membres du clan
RANK_KEYS = ("member", "elder", "coLeader", "leader")
RANK_NAMES = {
    "member": "Membre",
    "elder": "Aîné",
    "coLeader": "Chef adjoint",
    "leader": "Chef",
    ALL_KEY: "Tous les membres du clan",
}


def nick_for(name: str) -> str:
    """Pseudo Discord = nom en jeu (limite Discord : 32 caractères)."""
    return " ".join(name.split())[:32]


@dataclass
class SyncReport:
    found: bool = True                      # le compte Discord est bien sur le serveur
    nick: str | None = None                 # nouveau pseudo si modifié
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.nick or self.added or self.removed)

    def summary(self) -> str:
        if not self.found:
            return "Compte Discord absent du serveur."
        parts = []
        if self.nick:
            parts.append(f"pseudo → **{discord.utils.escape_markdown(self.nick)}**")
        if self.added:
            parts.append("rôles ajoutés : " + ", ".join(self.added))
        if self.removed:
            parts.append("rôles retirés : " + ", ".join(self.removed))
        text = " · ".join(parts) or "déjà à jour"
        if self.warnings:
            text += "\n" + "\n".join(f"⚠️ {w}" for w in self.warnings)
        return text


@dataclass
class Plan:
    """Écart entre l'état souhaité (selon le clan) et l'état réel sur Discord."""
    found: bool = True                       # compte lié ET présent sur le serveur
    blocked: bool = False                    # impossible de conclure (serveur/liste du clan indisponibles)
    member: object | None = None
    guild: object | None = None
    in_clan: bool = False
    current_nick: str | None = None
    expected_nick: str | None = None         # pseudo attendu (None si renommage désactivé / hors clan)
    new_nick: str | None = None              # pseudo à appliquer s'il diffère
    to_add: list = field(default_factory=list)
    to_remove: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not (self.new_nick or self.to_add or self.to_remove)


class RoleSync:
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ accès
    def guild(self) -> discord.Guild | None:
        cfg = self.bot.cfg
        if cfg.guild_id:
            return self.bot.get_guild(cfg.guild_id)
        return self.bot.guilds[0] if len(self.bot.guilds) == 1 else None

    async def _get_member(self, guild: discord.Guild, discord_id: int) -> discord.Member | None:
        member = guild.get_member(discord_id)
        if member:
            return member
        try:
            return await guild.fetch_member(discord_id)
        except discord.NotFound:
            return None
        except discord.HTTPException:
            log.warning("Impossible de récupérer le membre Discord %s", discord_id)
            return None

    async def rename_enabled(self) -> bool:
        return (await self.bot.db.get_meta("rename_enabled")) != "0"   # activé par défaut

    # ------------------------------------------------------------------ plan (lecture seule) puis application
    async def plan(self, tag: str) -> "Plan":
        """Calcule ce qu'il faudrait changer pour ce joueur, sans rien modifier."""
        plan = Plan()
        db = self.bot.db
        player = await db.get_player(tag)
        if not player or not player.get("discord_id"):
            plan.found = False
            return plan
        guild = self.guild()
        if guild is None:
            plan.blocked = True
            plan.warnings.append("Serveur Discord introuvable (règle-le avec /configurer).")
            return plan
        member = await self._get_member(guild, player["discord_id"])
        if member is None:
            plan.found = False
            return plan
        plan.member, plan.guild, plan.current_nick = member, guild, member.display_name
        if not self.bot.members:                  # liste du clan pas encore chargée : ne rien retirer à tort
            plan.blocked = True
            plan.warnings.append("Liste des membres du clan pas encore chargée, réessaie dans un instant.")
            return plan

        info = self.bot.members.get(tag)          # présent = actuellement dans le clan
        plan.in_clan = info is not None
        mapping = await db.rank_roles()
        managed = set(mapping.values())
        desired: set[int] = set()
        if plan.in_clan:
            if ALL_KEY in mapping:
                desired.add(mapping[ALL_KEY])
            if info.get("role") in mapping:
                desired.add(mapping[info["role"]])

        # -- pseudo
        if await self.rename_enabled() and plan.in_clan:
            nick = nick_for(info["name"])
            plan.expected_nick = nick
            if member.id == guild.owner_id:
                plan.warnings.append("Le propriétaire du serveur ne peut pas être renommé.")
            elif member.display_name != nick:
                plan.new_nick = nick

        # -- rôles
        current = {r.id: r for r in member.roles}
        for rid in desired - current.keys():
            role = guild.get_role(rid)
            if role is None:
                plan.warnings.append(f"Le rôle configuré ({rid}) n'existe plus.")
            elif not role.is_assignable():
                plan.warnings.append(f"Je ne peux pas attribuer @{role.name} : place mon rôle au-dessus.")
            else:
                plan.to_add.append(role)
        for rid in managed - desired:
            role = current.get(rid)
            if role is not None and role.is_assignable():
                plan.to_remove.append(role)
        return plan

    async def apply(self, tag: str) -> SyncReport:
        plan = await self.plan(tag)
        report = SyncReport(found=plan.found, warnings=list(plan.warnings))
        if not plan.found or plan.blocked:
            return report
        member = plan.member
        if plan.new_nick:
            try:
                await member.edit(nick=plan.new_nick, reason="Synchro Clash Royale")
                report.nick = plan.new_nick
            except discord.Forbidden:
                report.warnings.append("Renommage impossible : place le rôle du bot au-dessus du membre.")
            except discord.HTTPException:
                log.exception("Renommage impossible pour %s", member)
        try:
            if plan.to_add:
                await member.add_roles(*plan.to_add, reason="Synchro Clash Royale")
                report.added = [r.name for r in plan.to_add]
            if plan.to_remove:
                await member.remove_roles(*plan.to_remove, reason="Synchro Clash Royale")
                report.removed = [r.name for r in plan.to_remove]
        except discord.Forbidden:
            report.warnings.append("Permission « Gérer les rôles » manquante.")
        except discord.HTTPException:
            log.exception("Mise à jour des rôles impossible pour %s", member)
        return report

    async def apply_safe(self, tag: str) -> SyncReport | None:
        """Comme apply() mais n'échoue jamais (utilisé par les tâches de fond)."""
        try:
            return await self.apply(tag)
        except Exception:
            log.exception("Synchro Discord impossible pour %s", tag)
            return None

    async def sync_all(self) -> dict[str, int]:
        stats = {"maj": 0, "a_jour": 0, "absents": 0, "avertissements": 0}
        for player in await self.bot.db.linked_players():
            report = await self.apply_safe(player["tag"])
            if report is None:
                continue
            if not report.found:
                stats["absents"] += 1
            elif report.changed:
                stats["maj"] += 1
            else:
                stats["a_jour"] += 1
            stats["avertissements"] += len(report.warnings)
        return stats

    async def strip(self, discord_id: int) -> None:
        """Retire tous les rôles gérés par le bot (utilisé par /delier)."""
        guild = self.guild()
        if guild is None:
            return
        member = await self._get_member(guild, discord_id)
        if member is None:
            return
        managed = set((await self.bot.db.rank_roles()).values())
        roles = [r for r in member.roles if r.id in managed and r.is_assignable()]
        if roles:
            await member.remove_roles(*roles, reason="Délié de Clash Royale")
