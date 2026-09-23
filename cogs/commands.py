"""Commandes slash : /clan /membres /joueur /guerre /decks /historique_guerre
/moyennes /inactifs /dons /lier"""
from __future__ import annotations

import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from config import normalize_tag
from embeds import (
    PERIOD_LABELS,
    ROLE_SHORT,
    build_decks_embed,
    build_player_embed,
    clan_url,
    cap,
    esc,
    fmt_int,
    since,
)
from helpers import handle_command_error, member_autocomplete, require_config, resolve_tag
from history import build_history
from ui import can_edit
from war_utils import decks_remaining

log = logging.getLogger("commands")

CLAN_TYPES = {"open": "Ouvert", "inviteOnly": "Sur invitation", "closed": "Fermé"}


class ClashCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ utilitaires
    async def resolve_tag(self, value: str) -> str:
        return await resolve_tag(self.bot, value)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_config(interaction, clan=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await handle_command_error(interaction, error)

    # ------------------------------------------------------------------ /clan
    @app_commands.command(name="clan", description="Informations sur ton clan (ou sur n'importe quel clan avec son tag)")
    @app_commands.describe(tag="Tag d'un autre clan (facultatif)")
    async def clan(self, interaction: discord.Interaction, tag: str | None = None):
        await interaction.response.defer()
        tag = normalize_tag(tag) if tag else self.bot.cfg.clan_tag
        c = await self.bot.api.clan(tag, ttl=30)
        embed = discord.Embed(
            title=f"🏰 {c['name']}", url=clan_url(c["tag"]), description=esc(c.get("description", "")), color=discord.Color.blurple()
        )
        embed.add_field(name="Tag", value=f"`{c['tag']}`")
        embed.add_field(name="Membres", value=f"{c.get('members', 0)}/50")
        embed.add_field(name="Type", value=CLAN_TYPES.get(c.get("type"), c.get("type", "?")))
        embed.add_field(name="Trophées requis", value=fmt_int(c.get("requiredTrophies", 0)))
        embed.add_field(name="Score du clan", value=fmt_int(c.get("clanScore", 0)))
        embed.add_field(name="Trophées de guerre", value=fmt_int(c.get("clanWarTrophies", 0)))
        embed.add_field(name="Dons / semaine", value=fmt_int(c.get("donationsPerWeek", 0)))
        if c.get("clanChestLevel"):
            embed.add_field(name="Coffre de clan", value=f"Niveau {c['clanChestLevel']} · {c.get('clanChestStatus', '?')}")
        if c.get("location", {}).get("name"):
            embed.add_field(name="Pays", value=c["location"]["name"])
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ /membres
    @app_commands.command(name="membres", description="Liste des membres du clan")
    @app_commands.describe(tri="Critère de tri")
    @app_commands.choices(
        tri=[
            app_commands.Choice(name="Rang du clan", value="clanRank"),
            app_commands.Choice(name="Trophées", value="trophies"),
            app_commands.Choice(name="Dons", value="donations"),
        ]
    )
    async def membres(self, interaction: discord.Interaction, tri: str = "clanRank"):
        await interaction.response.defer()
        c = await self.bot.api.clan(self.bot.cfg.clan_tag, ttl=30)
        members = c.get("memberList", [])
        members.sort(key=lambda m: m.get(tri, 0), reverse=(tri != "clanRank"))
        lines = [f"{'#':>2} {'Nom':<14} {'R':<2} {'Troph.':>6} {'Dons':>5}"]
        for i, m in enumerate(members, 1):
            lines.append(
                f"{i:>2} {m['name'][:14]:<14} {ROLE_SHORT.get(m.get('role'), '?'):<2} "
                f"{m.get('trophies', 0):>6} {m.get('donations', 0):>5}"
            )
        embed = discord.Embed(
            title=f"👥 Membres ({len(members)})", description="```\n" + "\n".join(lines) + "\n```", color=discord.Color.blurple()
        )
        embed.set_footer(text="R : M=Membre A=Aîné CA=Chef adjoint C=Chef")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ /joueur
    @app_commands.command(name="joueur", description="Fiche complète d'un joueur (clans, guerres, passages)")
    @app_commands.describe(joueur="Nom en jeu ou tag")
    @app_commands.autocomplete(joueur=member_autocomplete)
    async def joueur(self, interaction: discord.Interaction, joueur: str):
        await interaction.response.defer()
        tag = await self.resolve_tag(joueur)
        profile = await self.bot.api.player(tag)
        current_clan = (profile.get("clan") or {}).get("tag")
        history = await build_history(
            self.bot.api, self.bot.db, tag, self.bot.cfg.clan_tag, extra_clans=(current_clan,)
        )
        embed = build_player_embed(
            profile=profile,
            stints=await self.bot.db.stints(tag),
            history=history,
            mode="profile",
        )
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ /guerre
    @app_commands.command(name="guerre", description="État de la guerre de clans en cours")
    async def guerre(self, interaction: discord.Interaction):
        await interaction.response.defer()
        cfg = self.bot.cfg
        race, war_day, rows = await decks_remaining(self.bot.api, cfg.clan_tag)
        clans = sorted(race.get("clans", []), key=lambda c: c.get("fame", 0), reverse=True)
        lines = []
        for i, c in enumerate(clans, 1):
            mark = "👉 " if c.get("tag") == cfg.clan_tag else ""
            done = " 🏁" if c.get("finishTime") else ""
            lines.append(f"{i}. {mark}**{esc(c['name'])}** — {fmt_int(c.get('fame', 0))} pts{done}")
        embed = discord.Embed(
            title=f"⚔️ {PERIOD_LABELS.get(race.get('periodType'), 'Guerre')} — Semaine {race.get('sectionIndex', 0) + 1}",
            description="\n".join(lines) or "Pas de course en cours.",
            color=discord.Color.orange(),
        )
        if war_day:
            left = sum(4 - used for _n, _t, used in rows)
            embed.add_field(name="Decks restants aujourd'hui", value=f"{left} ({len(rows)} joueurs concernés)")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ /guerre_joueurs
    @app_commands.command(name="guerre_joueurs", description="Classement des joueurs de ton clan dans la guerre en cours")
    async def guerre_joueurs(self, interaction: discord.Interaction):
        await interaction.response.defer()
        race = await self.bot.api.current_river_race(self.bot.cfg.clan_tag, ttl=20)
        parts = sorted(race.get("clan", {}).get("participants", []), key=lambda p: p.get("fame", 0), reverse=True)
        lines = [f"{'#':>2} {'Nom':<14} {'Gloire':>6} {'Decks':>5} {'Auj.':>4} {'Bat.':>4}"]
        for i, p in enumerate(parts[:50], 1):
            lines.append(
                f"{i:>2} {p['name'][:14]:<14} {p.get('fame', 0):>6} {p.get('decksUsed', 0):>5} "
                f"{p.get('decksUsedToday', 0):>4} {p.get('boatAttacks', 0):>4}"
            )
        embed = discord.Embed(
            title=f"⚔️ Guerre en cours — {len(parts)} participants",
            description="```\n" + cap("\n".join(lines), 3900) + "\n```",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Decks = decks joués cette semaine · Auj. = aujourd'hui · Bat. = attaques de bateau")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ /decks
    @app_commands.command(name="decks", description="Qui n'a pas fini ses 4 decks de guerre aujourd'hui ?")
    async def decks(self, interaction: discord.Interaction):
        await interaction.response.defer()
        _race, war_day, rows = await decks_remaining(self.bot.api, self.bot.cfg.clan_tag)
        if not war_day:
            await interaction.followup.send("Pas de jour de guerre en cours (période d'entraînement).")
        elif not rows:
            await interaction.followup.send("✅ Tout le monde a joué ses decks aujourd'hui !")
        else:
            await interaction.followup.send(embed=build_decks_embed(rows, "⏰ Decks restants aujourd'hui"))

    # ------------------------------------------------------------------ /historique_guerre
    @app_commands.command(name="historique_guerre", description="Résultats des dernières guerres du clan")
    @app_commands.describe(nombre="Nombre de guerres (1-10)")
    async def historique_guerre(self, interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 10] = 5):
        await interaction.response.defer()
        cfg = self.bot.cfg
        lines = []
        for race in (await self.bot.api.river_race_log(cfg.clan_tag, limit=nombre))[:nombre]:
            s = next((s for s in race.get("standings", []) if s["clan"]["tag"] == cfg.clan_tag), None)
            if s:
                lines.append(
                    f"S{race['seasonId']} · Sem. {race['sectionIndex'] + 1} — **#{s['rank']}** — "
                    f"{fmt_int(s['clan'].get('fame', 0))} pts — {s.get('trophyChange', 0):+d} 🏆"
                )
        await interaction.followup.send(
            embed=discord.Embed(title="📜 Historique des guerres", description="\n".join(lines) or "Aucune donnée.", color=discord.Color.blurple())
        )

    # ------------------------------------------------------------------ /moyennes
    @app_commands.command(name="moyennes", description="Moyenne de gloire par joueur sur les dernières guerres du clan")
    @app_commands.describe(guerres="Nombre de guerres prises en compte (1-10)")
    async def moyennes(self, interaction: discord.Interaction, guerres: app_commands.Range[int, 1, 10] = 5):
        await interaction.response.defer()
        cfg = self.bot.cfg
        totals: dict[str, list[int]] = defaultdict(list)
        decks: dict[str, list[int]] = defaultdict(list)
        for race in (await self.bot.api.river_race_log(cfg.clan_tag, limit=guerres))[:guerres]:
            s = next((s for s in race.get("standings", []) if s["clan"]["tag"] == cfg.clan_tag), None)
            for p in (s or {}).get("clan", {}).get("participants", []):
                totals[p["tag"]].append(p.get("fame", 0))
                decks[p["tag"]].append(p.get("decksUsed", 0))
        rows = []
        for tag, m in self.bot.members.items():
            if tag in totals:
                rows.append((m["name"], sum(totals[tag]) / len(totals[tag]), len(totals[tag]), sum(decks[tag]) / len(decks[tag])))
        rows.sort(key=lambda r: r[1], reverse=True)
        lines = [f"{'Nom':<14} {'Moy.':>6} {'Guerres':>7} {'Decks':>5}"]
        lines += [f"{n[:14]:<14} {a:>6.0f} {c:>7} {d:>5.1f}" for n, a, c, d in rows]
        await interaction.followup.send(
            embed=discord.Embed(
                title=f"📊 Moyenne de gloire ({guerres} dernières guerres)",
                description="```\n" + cap("\n".join(lines), 3900) + "\n```",
                color=discord.Color.blurple(),
            )
        )

    # ------------------------------------------------------------------ /inactifs
    @app_commands.command(name="inactifs", description="Membres inactifs depuis N jours")
    @app_commands.describe(jours="Nombre de jours minimum d'inactivité")
    async def inactifs(self, interaction: discord.Interaction, jours: app_commands.Range[int, 1, 60] = 7):
        await interaction.response.defer()
        c = await self.bot.api.clan(self.bot.cfg.clan_tag, ttl=60)
        rows = []
        for m in c.get("memberList", []):
            if m.get("lastSeen"):
                days = since(m["lastSeen"]).days
                if days >= jours:
                    rows.append((days, m["name"], ROLE_SHORT.get(m.get("role"), "?")))
        rows.sort(reverse=True)
        text = "\n".join(f"• {esc(n)} ({r}) — {d} j" for d, n, r in rows[:40]) or "🎉 Personne !"
        await interaction.followup.send(
            embed=discord.Embed(title=f"💤 Inactifs depuis {jours} jour(s) ou plus ({len(rows)})", description=cap(text, 4000), color=discord.Color.dark_grey())
        )

    # ------------------------------------------------------------------ /dons
    @app_commands.command(name="dons", description="Top et flop des dons de la semaine")
    async def dons(self, interaction: discord.Interaction):
        await interaction.response.defer()
        c = await self.bot.api.clan(self.bot.cfg.clan_tag, ttl=60)
        members = c.get("memberList", [])
        top = sorted(members, key=lambda m: m.get("donations", 0), reverse=True)[:10]
        flop = sorted(members, key=lambda m: m.get("donations", 0))[:10]
        embed = discord.Embed(title="🎁 Dons de la semaine", color=discord.Color.green())
        embed.add_field(
            name="Top 10",
            value="\n".join(f"{i}. {esc(m['name'])} — {fmt_int(m.get('donations', 0))}" for i, m in enumerate(top, 1)),
            inline=True,
        )
        embed.add_field(
            name="Plus faibles (donnés / reçus)",
            value="\n".join(f"• {esc(m['name'])} — {m.get('donations', 0)} / {m.get('donationsReceived', 0)}" for m in flop),
            inline=True,
        )
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ /lier
    @app_commands.command(name="lier", description="Lier un compte Discord à un joueur (renomme et attribue le rôle du rang)")
    @app_commands.describe(joueur="Nom ou tag", utilisateur="Compte Discord")
    @app_commands.autocomplete(joueur=member_autocomplete)
    async def lier(self, interaction: discord.Interaction, joueur: str, utilisateur: discord.Member):
        tag = await self.resolve_tag(joueur)
        existing = await self.bot.db.get_player(tag)
        self_link = utilisateur.id == interaction.user.id and not (existing or {}).get("discord_id")
        if not (self_link or await can_edit(interaction, tag)):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        name = (self.bot.members.get(tag) or {}).get("name")
        await self.bot.db.set_discord_id(tag, utilisateur.id, name)
        report = await self.bot.sync.apply_safe(tag)
        await interaction.followup.send(
            f"🔗 `{tag}` lié à {utilisateur.mention}\n" + (report.summary() if report else ""),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot) -> None:
    await bot.add_cog(ClashCommands(bot))
