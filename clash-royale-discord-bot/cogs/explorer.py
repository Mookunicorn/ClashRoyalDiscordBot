"""Le reste de l'API Clash Royale : cartes, tournois, classements, pays, saisons, événements…

Regroupe tout ce qui n'est pas propre au clan suivi : ces commandes marchent sur n'importe
quel clan, joueur, tournoi ou pays, tant que la clé API est configurée (/configurer).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import normalize_tag
from cr_api import ClashAPIError, NotFound
from embeds import cap, esc, fmt_int
from helpers import handle_command_error, require_config

log = logging.getLogger("explorer")

RARITY_LABELS = {"common": "Commune", "rare": "Rare", "epic": "Épique", "legendary": "Légendaire", "champion": "Champion"}


def rows_table(header: str, rows: list[str], limit: int = 3900) -> str:
    return "```\n" + cap(header + "\n" + "\n".join(rows), limit) + "\n```"


class Explorer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_config(interaction, clan=False)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await handle_command_error(interaction, error)

    # ------------------------------------------------------------------ recherche de clans
    @app_commands.command(name="recherche_clans", description="Rechercher des clans par nom")
    @app_commands.describe(nom="Nom du clan", min_membres="Membres minimum", min_score="Score minimum")
    async def recherche_clans(self, interaction: discord.Interaction, nom: str,
                              min_membres: app_commands.Range[int, 1, 50] | None = None,
                              min_score: int | None = None):
        await interaction.response.defer()
        clans = await self.bot.api.clan_search(name=nom, min_members=min_membres, min_score=min_score, limit=10)
        if not clans:
            await interaction.followup.send("Aucun clan trouvé.")
            return
        lines = [f"{'#':>2} {'Nom':<20} {'Tag':<10} {'Mb':>3} {'Score':>7}"]
        for i, c in enumerate(clans, 1):
            lines.append(f"{i:>2} {c['name'][:20]:<20} {c['tag']:<10} {c.get('members', 0):>3} {c.get('clanScore', 0):>7}")
        await interaction.followup.send(embed=discord.Embed(
            title=f"🔎 Clans « {nom} »", description=rows_table("", lines), color=discord.Color.blurple()))

    # ------------------------------------------------------------------ joueur : coffres à venir
    @app_commands.command(name="coffres", description="Prochains coffres d'un joueur")
    @app_commands.describe(tag="Tag du joueur (#XXXX)")
    async def coffres(self, interaction: discord.Interaction, tag: str):
        await interaction.response.defer()
        tag = normalize_tag(tag)
        chests = await self.bot.api.upcoming_chests(tag)
        if not chests:
            await interaction.followup.send("Aucune information de coffre disponible.")
            return
        lines = [f"{i}. {c.get('name', '?')}" + (f" (dans {c['index']})" if c.get("index") is not None else "")
                for i, c in enumerate(chests[:15], 1)]
        await interaction.followup.send(embed=discord.Embed(
            title=f"📦 Prochains coffres — {tag}", description="\n".join(lines), color=discord.Color.gold()))

    # ------------------------------------------------------------------ ancien système de guerre (avant les River Race)
    @app_commands.command(name="ancienne_guerre", description="Ancien système de guerre de clan (avant les Guerres de clans actuelles)")
    @app_commands.describe(tag="Tag du clan (défaut : le tien)")
    async def ancienne_guerre(self, interaction: discord.Interaction, tag: str | None = None):
        await interaction.response.defer()
        tag = normalize_tag(tag) if tag else self.bot.cfg.clan_tag
        if not tag:
            await interaction.followup.send("Indique un tag de clan, ou configure le tien avec `/configurer`.")
            return
        try:
            war = await self.bot.api.current_war(tag)
        except ClashAPIError as exc:
            if exc.status == 403:
                await interaction.followup.send("Ce clan utilise les Guerres de clans actuelles : cet ancien système n'est plus disponible (utilise `/guerre`).")
                return
            raise
        state = war.get("state", "notInWar")
        if state == "notInWar":
            await interaction.followup.send("Ce clan n'est pas en guerre (ancien système).")
            return
        embed = discord.Embed(title=f"⚔️ Ancienne guerre de clan — {state}", color=discord.Color.orange())
        clan = war.get("clan", {})
        embed.add_field(name="Score du clan", value=fmt_int(clan.get("clanScore", 0)))
        participants = sorted(war.get("participants", []), key=lambda p: p.get("wins", 0), reverse=True)[:10]
        if participants:
            embed.add_field(name="Top participants", value="\n".join(
                f"{i}. {esc(p['name'])} — {p.get('wins', 0)}V/{p.get('battlesPlayed', 0)}B" for i, p in enumerate(participants, 1)), inline=False)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ cartes
    @app_commands.command(name="cartes", description="Liste des cartes disponibles (filtre par rareté possible)")
    @app_commands.choices(rarete=[app_commands.Choice(name=v, value=k) for k, v in RARITY_LABELS.items()])
    async def cartes(self, interaction: discord.Interaction, rarete: str | None = None):
        await interaction.response.defer()
        data = await self.bot.api.cards()
        cards = data.get("items", [])
        if rarete:
            cards = [c for c in cards if c.get("rarity") == rarete]
        lines = [f"• {c['name']} ({RARITY_LABELS.get(c.get('rarity'), c.get('rarity', '?'))}, {c.get('elixirCost', '?')} élixir)"
                for c in cards]
        title = "🃏 Cartes" + (f" — {RARITY_LABELS[rarete]}" if rarete else "") + f" ({len(cards)})"
        await interaction.followup.send(embed=discord.Embed(title=title, description=cap("\n".join(lines), 4000), color=discord.Color.blurple()))

    @app_commands.command(name="carte", description="Détails d'une carte")
    @app_commands.describe(nom="Nom de la carte")
    async def carte(self, interaction: discord.Interaction, nom: str):
        await interaction.response.defer()
        data = await self.bot.api.cards()
        card = next((c for c in data.get("items", []) if c["name"].lower() == nom.lower()), None)
        if card is None:
            matches = [c["name"] for c in data.get("items", []) if nom.lower() in c["name"].lower()]
            await interaction.followup.send(
                "Carte introuvable." + (f" Peut-être : {', '.join(matches[:5])} ?" if matches else "")
            )
            return
        embed = discord.Embed(title=f"🃏 {card['name']}", color=discord.Color.blurple())
        embed.add_field(name="Rareté", value=RARITY_LABELS.get(card.get("rarity"), card.get("rarity", "?")))
        embed.add_field(name="Coût en élixir", value=str(card.get("elixirCost", "?")))
        embed.add_field(name="Niveau max", value=str(card.get("maxLevel", "?")))
        icon = (card.get("iconUrls") or {}).get("medium")
        if icon:
            embed.set_thumbnail(url=icon)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ tournois
    @app_commands.command(name="tournois", description="Rechercher des tournois par nom")
    async def tournois(self, interaction: discord.Interaction, nom: str):
        await interaction.response.defer()
        results = await self.bot.api.tournament_search(nom, limit=10)
        if not results:
            await interaction.followup.send("Aucun tournoi trouvé.")
            return
        lines = [f"• **{esc(t['name'])}** `{t['tag']}` — {t.get('membersCount', t.get('capacity', '?'))} joueurs — {t.get('status', '?')}"  # noqa: E501
                for t in results]
        await interaction.followup.send(embed=discord.Embed(
            title=f"🏆 Tournois « {nom} »", description=cap("\n".join(lines), 4000), color=discord.Color.gold()))

    @app_commands.command(name="tournoi", description="Détails d'un tournoi")
    @app_commands.describe(tag="Tag du tournoi (#XXXX)")
    async def tournoi(self, interaction: discord.Interaction, tag: str):
        await interaction.response.defer()
        tag = normalize_tag(tag)
        t = await self.bot.api.tournament(tag)
        embed = discord.Embed(title=f"🏆 {t['name']}", description=esc(t.get("description", "")), color=discord.Color.gold())
        embed.add_field(name="Statut", value=t.get("status", "?"))
        embed.add_field(name="Type", value=t.get("type", "?"))
        embed.add_field(name="Joueurs", value=f"{t.get('membersCount', 0)}/{t.get('capacity', t.get('maxCapacity', '?'))}")
        level_cap = (t.get("levelCap") or {}).get("highestCardLevel")
        if level_cap is not None:
            embed.add_field(name="Niveau de carte plafonné", value=str(level_cap))
        top = sorted(t.get("membersList", []), key=lambda m: m.get("score", 0), reverse=True)[:10]
        if top:
            embed.add_field(name="Top 10", value="\n".join(f"{i}. {esc(m['name'])} — {m.get('score', 0)}" for i, m in enumerate(top, 1)), inline=False)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ pays / locations
    @app_commands.command(name="pays", description="Rechercher un pays / une région (pour les classements)")
    async def pays(self, interaction: discord.Interaction, nom: str):
        await interaction.response.defer()
        locations = await self.bot.api.locations()
        matches = [loc for loc in locations if nom.lower() in loc.get("name", "").lower()]
        if not matches:
            await interaction.followup.send("Aucun pays trouvé.")
            return
        lines = [f"• **{esc(loc['name'])}** — id `{loc['id']}`" + (" 🌍" if loc.get("isCountry") is False else "") for loc in matches[:20]]
        await interaction.followup.send(embed=discord.Embed(
            title=f"🌍 Pays « {nom} »", description="\n".join(lines), color=discord.Color.blurple()))

    async def resolve_location(self, value: str) -> tuple[str | int, str]:
        if value.lower() == "global":
            return "global", "🌍 Mondial"
        if value.isdigit():
            loc = await self.bot.api.location(value)
            return loc["id"], loc["name"]
        locations = await self.bot.api.locations()
        match = next((loc for loc in locations if loc.get("name", "").lower() == value.lower()), None)
        if match is None:
            match = next((loc for loc in locations if value.lower() in loc.get("name", "").lower()), None)
        if match is None:
            raise NotFound(404, f"pays « {value} » introuvable")
        return match["id"], match["name"]

    # ------------------------------------------------------------------ classements
    @app_commands.command(name="classement", description="Classement des clans, joueurs ou guerres de clan pour un pays")
    @app_commands.describe(type="Type de classement", pays="Nom du pays, ou « global » (défaut)")
    @app_commands.choices(type=[app_commands.Choice(name="Clans", value="clans"),
                                app_commands.Choice(name="Joueurs", value="players"),
                                app_commands.Choice(name="Guerres de clan", value="clanwars")])
    async def classement(self, interaction: discord.Interaction, type: str, pays: str = "global"):
        await interaction.response.defer()
        location_id, location_name = await self.resolve_location(pays)
        rows = await self.bot.api.ranking(type, location_id, limit=25)
        if type == "clans":
            lines = [f"{r['rank']:>3}. {esc(r['clanName'])[:24]:<24} {fmt_int(r.get('clanScore', 0)):>7}" for r in rows]
        elif type == "players":
            lines = [f"{r['rank']:>3}. {esc(r['name'])[:24]:<24} {fmt_int(r.get('trophies', 0)):>7}" for r in rows]
        else:
            lines = [f"{r['rank']:>3}. {esc(r['clanName'])[:24]:<24} {fmt_int(r.get('clanScore', 0)):>7}" for r in rows]
        icon = {"clans": "🏰", "players": "🏆", "clanwars": "⚔️"}[type]
        await interaction.followup.send(embed=discord.Embed(
            title=f"{icon} Classement {dict(clans='clans', players='joueurs', clanwars='guerres de clan')[type]} — {location_name}",
            description=rows_table("", lines), color=discord.Color.blurple()))

    @app_commands.command(name="classement_legende", description="Classement Path of Legends pour un pays")
    async def classement_legende(self, interaction: discord.Interaction, pays: str = "global"):
        await interaction.response.defer()
        location_id, location_name = await self.resolve_location(pays)
        rows = await self.bot.api.pathoflegend_players(location_id, limit=25)
        lines = [f"{r.get('rank', i):>3}. {esc(r['name'])[:24]:<24} {fmt_int(r.get('eloRating', r.get('trophies', 0))):>7}"
                for i, r in enumerate(rows, 1)]
        await interaction.followup.send(embed=discord.Embed(
            title=f"🏅 Path of Legends — {location_name}", description=rows_table("", lines), color=discord.Color.blurple()))

    # ------------------------------------------------------------------ saisons
    @app_commands.command(name="saisons", description="Dernières saisons de ligue (classement joueurs)")
    async def saisons(self, interaction: discord.Interaction):
        await interaction.response.defer()
        seasons = await self.bot.api.seasons()
        lines = [f"• `{s['id']}`" for s in seasons[-15:][::-1]]
        await interaction.followup.send(embed=discord.Embed(
            title="📅 Saisons de ligue", description="\n".join(lines) or "Aucune donnée.", color=discord.Color.blurple()))

    @app_commands.command(name="saison", description="Classement joueurs d'une saison de ligue")
    @app_commands.describe(id="Identifiant de la saison (voir /saisons)")
    async def saison(self, interaction: discord.Interaction, id: str):
        await interaction.response.defer()
        rows = await self.bot.api.season_players(id, limit=25)
        lines = [f"{i:>3}. {esc(r['name'])[:24]:<24} {fmt_int(r.get('trophies', 0)):>7}" for i, r in enumerate(rows, 1)]
        await interaction.followup.send(embed=discord.Embed(
            title=f"📅 Saison {id} — Top joueurs", description=rows_table("", lines) if lines else "Aucune donnée.",
            color=discord.Color.blurple()))

    # ------------------------------------------------------------------ événements, classements spéciaux, tournois mondiaux
    @app_commands.command(name="evenements", description="Événements en cours dans Clash Royale")
    async def evenements(self, interaction: discord.Interaction):
        await interaction.response.defer()
        events = await self.bot.api.events()
        if not events:
            await interaction.followup.send("Aucun événement en cours.")
            return
        lines = [f"• **{esc(e.get('type', e.get('name', '?')))}**" + (f" — {esc(e['name'])}" if e.get("type") and e.get("name") else "")
                for e in events]
        await interaction.followup.send(embed=discord.Embed(title="🎪 Événements en cours", description="\n".join(lines[:20]), color=discord.Color.purple()))

    @app_commands.command(name="classements_speciaux", description="Liste des classements spéciaux disponibles (leaderboards)")
    async def classements_speciaux(self, interaction: discord.Interaction):
        await interaction.response.defer()
        boards = await self.bot.api.leaderboards()
        lines = [f"• `{b['id']}` — {esc(b.get('name', '?'))}" for b in boards]
        await interaction.followup.send(embed=discord.Embed(
            title="📊 Classements spéciaux", description="\n".join(lines) or "Aucun.", color=discord.Color.blurple()))

    @app_commands.command(name="classement_special", description="Voir un classement spécial (leaderboard) par identifiant")
    async def classement_special(self, interaction: discord.Interaction, id: str):
        await interaction.response.defer()
        rows = await self.bot.api.leaderboard(id, limit=25)
        lines = [f"{r.get('rank', i):>3}. {esc(r.get('name', '?'))[:24]:<24} {fmt_int(r.get('score', r.get('trophies', 0))):>7}"
                for i, r in enumerate(rows, 1)]
        await interaction.followup.send(embed=discord.Embed(
            title=f"📊 Classement spécial {id}", description=rows_table("", lines) if lines else "Aucune donnée.",
            color=discord.Color.blurple()))

    @app_commands.command(name="tournois_mondiaux", description="Liste des tournois mondiaux en cours")
    async def tournois_mondiaux(self, interaction: discord.Interaction):
        await interaction.response.defer()
        tournaments = await self.bot.api.global_tournaments()
        lines = [f"• **{esc(t.get('name', '?'))}** `{t.get('tag', '?')}`" for t in tournaments]
        await interaction.followup.send(embed=discord.Embed(
            title="🌐 Tournois mondiaux", description="\n".join(lines[:20]) or "Aucun en cours.", color=discord.Color.gold()))


async def setup(bot) -> None:
    await bot.add_cog(Explorer(bot))
