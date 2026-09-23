"""Vérification des joueurs : existence du tag, présence dans le clan, liaison Discord, pseudo et rôles."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cr_api import NotFound
from database import parse_iso
from embeds import ROLE_LABELS, cap, dt, esc, fmt_int
from helpers import handle_command_error, member_autocomplete, require_config, resolve_tag
from ui import is_staff

log = logging.getLogger("verification")
ICON = {"ok": "✅", "warn": "⚠️", "error": "❌"}


def more(items: list[str], limit: int = 15) -> str:
    text = "\n".join(items[:limit])
    return text + (f"\n… et {len(items) - limit} de plus" if len(items) > limit else "")


# ---------------------------------------------------------------------- audit d'un joueur
async def audit_player(bot, tag: str):
    """Retourne ([(niveau, texte)], plan de synchro ou None)."""
    checks: list[tuple[str, str]] = []

    def add(level: str, text: str) -> None:
        checks.append((level, text))

    try:
        profile = await bot.api.player(tag)
    except NotFound:
        add("error", f"Le tag `{tag}` n'existe pas.")
        return checks, None
    add("ok", f"Tag valide : **{esc(profile.get('name', '?'))}** — {fmt_int(profile.get('trophies', 0))} 🏆, niveau roi {profile.get('expLevel', '?')}")

    info = bot.members.get(tag)
    current_clan = profile.get("clan") or {}
    if info:
        add("ok", f"Membre du clan (**{ROLE_LABELS.get(info.get('role'), info.get('role', '?'))}**)")
    else:
        where = f"actuellement dans **{esc(current_clan['name'])}**" if current_clan.get("name") else "sans clan"
        add("warn", f"Pas dans le clan ({where})")

    stints = await bot.db.stints(tag)
    add("ok" if stints else "warn", f"{len(stints)} passage(s) suivi(s) dans le clan" if stints else "Jamais suivi dans le clan")

    row = await bot.db.get_player(tag)
    plan = None
    if not row or not row.get("discord_id"):
        add("warn", "Aucun compte Discord lié")
        return checks, None
    mention = f"<@{row['discord_id']}>"
    if row.get("verified_at"):
        add("ok", f"Lié à {mention} — identité **vérifiée par jeton API** le {dt(parse_iso(row['verified_at']))}")
    else:
        add("warn", f"Lié à {mention} — **non vérifié par jeton** (lié par le staff ou en mode « clan »)")

    plan = await bot.sync.plan(tag)
    if not plan.found:
        add("error", "Le compte Discord lié n'est plus sur le serveur")
    elif plan.blocked:
        add("warn", "Impossible de comparer pseudo et rôles pour le moment")
    else:
        if plan.new_nick:
            add("warn", f"Pseudo actuel **{esc(plan.current_nick)}** ≠ attendu **{esc(plan.new_nick)}**")
        elif plan.expected_nick:
            add("ok", f"Pseudo conforme (**{esc(plan.expected_nick)}**)")
        if plan.to_add:
            add("warn", "Rôles manquants : " + ", ".join(f"@{r.name}" for r in plan.to_add))
        if plan.to_remove:
            add("warn", "Rôles en trop : " + ", ".join(f"@{r.name}" for r in plan.to_remove))
        if not plan.to_add and not plan.to_remove:
            add("ok", "Rôles conformes")
    for warning in (plan.warnings if plan else []):
        add("warn", warning)
    return checks, plan


# ---------------------------------------------------------------------- audit du clan
async def audit_clan(bot) -> dict[str, list[str]]:
    members = bot.members
    linked = {r["tag"]: r for r in await bot.db.linked_players()}
    report: dict[str, list[str]] = {k: [] for k in
                                    ("non_lies", "non_verifies", "anciens_lies", "absents", "desynchro", "orphelins")}
    for tag, m in members.items():
        if tag not in linked:
            report["non_lies"].append(esc(m["name"]))
        elif not linked[tag].get("verified_at"):
            report["non_verifies"].append(f"{esc(m['name'])} → <@{linked[tag]['discord_id']}>")
    for tag, row in linked.items():
        plan = await bot.sync.plan(tag)
        label = f"{esc(row['name'])} → <@{row['discord_id']}>"
        if tag not in members and not plan.blocked:
            report["anciens_lies"].append(label + (" (rôles à retirer)" if plan.to_remove else ""))
        if not plan.found:
            report["absents"].append(label)
        elif not plan.blocked and not plan.in_sync and tag in members:
            report["desynchro"].append(label)
    guild = bot.sync.guild()
    if guild is not None and bot.cfg.members_intent:       # rôles de rang portés par des comptes non liés
        managed = set((await bot.db.rank_roles()).values())
        linked_ids = {r["discord_id"] for r in linked.values()}
        for member in getattr(guild, "members", []):
            if not member.bot and member.id not in linked_ids and any(r.id in managed for r in member.roles):
                report["orphelins"].append(f"<@{member.id}>")
    return report


class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_staff(interaction):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return False
        return await require_config(interaction, clan=True)

    async def cog_app_command_error(self, interaction, error):
        await handle_command_error(interaction, error)

    @app_commands.command(name="verifier", description="Vérifier un joueur : tag, clan, liaison Discord, pseudo et rôles")
    @app_commands.describe(joueur="Nom en jeu ou tag", utilisateur="Ou le compte Discord à vérifier",
                           corriger="Appliquer la correction (pseudo + rôles) si besoin")
    @app_commands.autocomplete(joueur=member_autocomplete)
    async def verifier(self, interaction: discord.Interaction, joueur: str | None = None,
                       utilisateur: discord.Member | None = None, corriger: bool = False):
        await interaction.response.defer(ephemeral=True)
        if utilisateur is not None:
            row = await self.bot.db.find_by_discord(utilisateur.id)
            if row is None:
                await interaction.followup.send(f"{utilisateur.mention} n'est lié à aucun joueur Clash Royale.",
                                                allowed_mentions=discord.AllowedMentions.none())
                return
            tag = row["tag"]
        elif joueur:
            tag = await resolve_tag(self.bot, joueur)
        else:
            await interaction.followup.send("Indique un joueur (nom ou tag) ou un utilisateur Discord.")
            return
        checks, plan = await audit_player(self.bot, tag)
        note = ""
        if corriger and plan is not None and not plan.blocked and plan.found and not plan.in_sync:
            report = await self.bot.sync.apply_safe(tag)
            note = "\n\n🔧 **Correction appliquée** : " + (report.summary() if report else "échec")
        worst = "error" if any(l == "error" for l, _ in checks) else "warn" if any(l == "warn" for l, _ in checks) else "ok"
        embed = discord.Embed(
            title=f"🔎 Vérification de {tag}",
            description=cap("\n".join(f"{ICON[l]} {t}" for l, t in checks) + note, 4000),
            color={"ok": discord.Color.green(), "warn": discord.Color.orange(), "error": discord.Color.red()}[worst],
        )
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="verifier_tous", description="Audit complet : comptes non liés, non vérifiés, désynchronisés…")
    @app_commands.describe(corriger="Resynchroniser pseudos et rôles de tous les comptes liés")
    async def verifier_tous(self, interaction: discord.Interaction, corriger: bool = False):
        await interaction.response.defer(ephemeral=True)
        r = await audit_clan(self.bot)
        embed = discord.Embed(title="🔎 Audit du clan", color=discord.Color.blurple())
        sections = (
            ("non_lies", "🔗 Membres du clan sans compte Discord lié"),
            ("non_verifies", "⚠️ Liés sans vérification par jeton"),
            ("desynchro", "🔄 Pseudo ou rôles à corriger"),
            ("anciens_lies", "🚪 Liés mais partis du clan"),
            ("absents", "👻 Liés mais absents du Discord"),
            ("orphelins", "🎭 Rôles de rang sur des comptes non liés"),
        )
        for key, title in sections:
            items = r[key]
            embed.add_field(name=f"{title} ({len(items)})", value=cap(more(items) if items else "Aucun ✅"), inline=False)
        if corriger:
            stats = await self.bot.sync.sync_all()
            embed.set_footer(text=f"Correction : {stats['maj']} mis à jour · {stats['a_jour']} déjà à jour")
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot) -> None:
    await bot.add_cog(Verification(bot))
