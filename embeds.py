"""Construction des embeds Discord."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from database import parse_iso
from history import PlayerHistory

ROLE_LABELS = {"member": "Membre", "elder": "Aîné", "coLeader": "Chef adjoint", "leader": "Chef"}
ROLE_SHORT = {"member": "M", "elder": "A", "coLeader": "CA", "leader": "C"}
ROLE_RANK = {"member": 0, "elder": 1, "coLeader": 2, "leader": 3}
PERIOD_LABELS = {"training": "Entraînement", "warDay": "Jour de guerre", "colosseum": "Colisée"}
MEDALS = ["🥇", "🥈", "🥉"]


MAX_DISPLAY_LEVEL = 16  # niveau maximal affiché en jeu ; l'API donne un niveau relatif à la rareté


def display_level(card: dict) -> int:
    """Niveau tel qu'affiché en jeu : level + (16 - maxLevel)."""
    level, max_level = card.get("level"), card.get("maxLevel")
    if level is None:
        return 0
    return level + (MAX_DISPLAY_LEVEL - max_level) if max_level else level


# ---------------------------------------------------------------------- utilitaires
def fmt_int(n) -> str:
    return f"{int(round(n)):,}".replace(",", "\u202f")


def esc(text: str) -> str:
    return discord.utils.escape_markdown(str(text))


def cap(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def dt(value: datetime, style: str = "d") -> str:
    return discord.utils.format_dt(value, style)


def ordinal_fois(n: int) -> str:
    return f"{n}{'ʳᵉ' if n == 1 else 'ᵉ'} fois"


def humanize_duration(delta: timedelta) -> str:
    days = delta.days
    if days >= 60:
        return f"{days // 30} mois"
    if days >= 1:
        return f"{days} jour{'s' if days > 1 else ''}"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} h"
    return "moins d'une heure"


def player_url(tag: str) -> str:
    return f"https://royaleapi.com/player/{tag.lstrip('#')}"


def clan_url(tag: str) -> str:
    return f"https://royaleapi.com/clan/{tag.lstrip('#')}"


def stint_line(stint: dict) -> str:
    start = parse_iso(stint["joined_at"])
    end = parse_iso(stint["left_at"]) if stint.get("left_at") else None
    if stint.get("initial"):
        if end:
            return f"suivi depuis le {dt(start)} jusqu'au {dt(end)}"
        return f"membre depuis au moins le {dt(start)} (en cours)"
    if end:
        return f"du {dt(start)} au {dt(end)}"
    return f"depuis le {dt(start)} (en cours)"


# ---------------------------------------------------------------------- fiche joueur / arrivée
def build_player_embed(
    *,
    profile: dict,
    stints: list[dict],
    history: PlayerHistory | None,
    mode: str,            # "join" ou "profile"
    loading: bool = False,  # True : historique en cours de chargement (envoi immédiat puis mise à jour)
) -> discord.Embed:
    tag = profile["tag"]
    name = profile.get("name", tag)

    if mode == "join":
        embed = discord.Embed(
            title=f"🎉 Nouveau membre : {name}",
            description=f"**{esc(name)}** vient de rejoindre le clan !",
            color=discord.Color.green(),
        )
    else:
        embed = discord.Embed(title=f"👤 {name}", color=discord.Color.blurple())
    embed.url = player_url(tag)

    # 1. Nom
    embed.add_field(name="🎮 Nom en jeu", value=f"[{esc(name)}]({player_url(tag)})\n`{tag}`", inline=True)
    # niveau / trophées
    trophies = profile.get("trophies")
    if trophies is not None:
        best = profile.get("bestTrophies")
        arena = (profile.get("arena") or {}).get("name", "")
        lines = [f"**{fmt_int(trophies)}** 🏆" + (f" (record {fmt_int(best)})" if best else "")]
        if profile.get("expLevel"):
            lines.append(f"Niveau roi {profile['expLevel']}")
        if arena:
            lines.append(arena)
        embed.add_field(name="📊 Profil", value="\n".join(lines), inline=True)

    # 3. Nombre de passages dans le clan
    count = len(stints)
    if mode == "join":
        head = f"**{ordinal_fois(count)}** dans le clan"
    elif count:
        head = f"**{count}** passage{'s' if count > 1 else ''} suivi{'s' if count > 1 else ''}"
    else:
        head = "Jamais suivi dans le clan"
    body = [head]
    past = [s for s in stints if s.get("left_at")]
    for s in past[-4:]:
        body.append(f"• {stint_line(s)}")
    if mode == "profile" and stints and not stints[-1].get("left_at"):
        body.append(f"• {stint_line(stints[-1])}")
    if history and count <= 1 and history.wars_in_own_clan > 0 and mode == "join":
        body.append(
            f"⚠️ Ancien membre probable : vu dans {history.wars_in_own_clan} guerre(s) "
            "du clan avant le suivi du bot"
        )
    embed.add_field(name="🔁 Passages dans le clan", value=cap("\n".join(body)), inline=False)

    # 4. 5 derniers clans
    if loading and history is None:
        embed.add_field(name="🏰 5 derniers clans", value="⏳ Chargement…", inline=False)
        embed.add_field(name="⚔️ Moyenne des 5 dernières guerres", value="⏳ Chargement…", inline=False)
        return embed
    if history and history.previous_clans:
        lines = []
        for i, c in enumerate(history.previous_clans, 1):
            seen = dt(parse_iso(c["last_seen"]))
            lines.append(
                f"{i}. [{esc(c['clan_name'] or c['clan_tag'])}]({clan_url(c['clan_tag'])}) "
                f"`{c['clan_tag']}` — vu le {seen}"
            )
        value = "\n".join(lines)
    else:
        value = "*Aucun autre clan détecté dans ses derniers combats.*"
    embed.add_field(name="🏰 5 derniers clans", value=cap(value), inline=False)

    # 5. Moyenne des 5 dernières guerres
    if history and history.wars:
        lines = [
            f"**{fmt_int(history.avg_fame)} pts** de gloire en moyenne "
            f"sur {len(history.wars)} guerre{'s' if len(history.wars) > 1 else ''} "
            f"• {history.avg_decks:.1f} decks/guerre"
        ]
        for w in history.wars:
            lines.append(
                f"• {dt(w.date)} — {fmt_int(w.fame)} pts ({w.decks}/16 decks) — "
                f"{esc(' + '.join(dict.fromkeys(w.clans)))}"
            )
        value = "\n".join(lines)
    else:
        value = "*Aucune guerre trouvée (pas de participation récente ou historique indisponible).*"
    embed.add_field(name="⚔️ Moyenne des 5 dernières guerres", value=cap(value), inline=False)

    if mode == "profile":
        for name, value, inline in profile_extra_fields(profile):
            embed.add_field(name=name, value=cap(value), inline=inline)
    if history and history.error:
        embed.set_footer(text="Historique partiel : erreur lors de la récupération")
    return embed


def profile_extra_fields(profile: dict) -> list[tuple[str, str, bool]]:
    """Statistiques, Path of Legends et deck du joueur (tout est facultatif dans l'API)."""
    fields: list[tuple[str, str, bool]] = []
    wins, losses, total = profile.get("wins"), profile.get("losses"), profile.get("battleCount")
    stats = []
    if wins is not None and losses is not None:
        rate = f" ({100 * wins / (wins + losses):.0f} %)" if (wins + losses) else ""
        stats.append(f"Victoires / défaites : **{fmt_int(wins)}** / {fmt_int(losses)}{rate}")
    if total is not None:
        stats.append(f"Combats : {fmt_int(total)} · 3 couronnes : {fmt_int(profile.get('threeCrownWins', 0))}")
    if profile.get("totalDonations") is not None:
        stats.append(f"Dons totaux : {fmt_int(profile['totalDonations'])}")
    if profile.get("warDayWins") is not None:
        stats.append(f"Victoires en guerre : {fmt_int(profile['warDayWins'])} · cartes de clan : {fmt_int(profile.get('clanCardsCollected', 0))}")
    if profile.get("currentFavouriteCard", {}).get("name"):
        stats.append(f"Carte favorite : {profile['currentFavouriteCard']['name']}")
    if stats:
        fields.append(("📈 Statistiques", "\n".join(stats), False))

    pol = []
    for label, key in (("Saison en cours", "currentPathOfLegendSeasonResult"),
                       ("Saison précédente", "lastPathOfLegendSeasonResult"),
                       ("Meilleure saison", "bestPathOfLegendSeasonResult")):
        r = profile.get(key)
        if isinstance(r, dict) and (r.get("trophies") is not None or r.get("leagueNumber") is not None):
            rank = f" · rang mondial #{fmt_int(r['rank'])}" if r.get("rank") else ""
            pol.append(f"{label} : ligue {r.get('leagueNumber', '?')} — {fmt_int(r.get('trophies', 0))} 🏆{rank}")
    if pol:
        fields.append(("🏅 Path of Legends", "\n".join(pol), False))

    deck = profile.get("currentDeck") or []
    if deck:
        cards = ", ".join(f"{c.get('name', '?')} ({display_level(c)})" for c in deck)
        costs = [c["elixirCost"] for c in deck if c.get("elixirCost") is not None]
        avg = f"\nÉlixir moyen : {sum(costs) / len(costs):.1f}" if costs and len(costs) == len(deck) else ""
        fields.append(("🃏 Deck actuel", cards + avg, False))
    return fields


def build_leave_embed(player: dict | None, tag: str, stint: dict | None, count: int) -> discord.Embed:
    name = (player or {}).get("name", tag)
    embed = discord.Embed(
        title=f"👋 {name} a quitté le clan",
        color=discord.Color.red(),
        url=player_url(tag),
    )
    embed.add_field(name="🎮 Nom en jeu", value=f"{esc(name)}\n`{tag}`", inline=True)
    if stint:
        start = parse_iso(stint["joined_at"])
        end = parse_iso(stint["left_at"])
        prefix = "≥ " if stint.get("initial") else ""
        embed.add_field(name="⏱️ Durée dans le clan", value=prefix + humanize_duration(end - start), inline=True)
    embed.add_field(name="🔁 Passages suivis", value=str(count), inline=True)
    return embed


def build_role_embed(player: dict | None, m: dict, old: str, new: str) -> discord.Embed:
    up = ROLE_RANK.get(new, 0) > ROLE_RANK.get(old, 0)
    return discord.Embed(
        title=f"{'⬆️ Promotion' if up else '⬇️ Rétrogradation'} : {m['name']}",
        description=f"{ROLE_LABELS.get(old, old)} → **{ROLE_LABELS.get(new, new)}**",
        color=discord.Color.gold() if up else discord.Color.orange(),
        url=player_url(m["tag"]),
    )


# ---------------------------------------------------------------------- réglages du clan
CLAN_TYPE_LABELS = {"open": "🔓 Ouvert", "inviteOnly": "🔐 Sur invitation", "closed": "🔒 Fermé"}
CLAN_TYPE_COLORS = {"open": discord.Color.green(), "inviteOnly": discord.Color.orange(), "closed": discord.Color.red()}


def clan_snapshot(clan: dict) -> dict:
    """Réglages du clan surveillés (comparés d'un cycle à l'autre)."""
    return {
        "type": clan.get("type"),
        "requiredTrophies": clan.get("requiredTrophies"),
        "name": clan.get("name"),
        "description": clan.get("description"),
        "location": (clan.get("location") or {}).get("name"),
    }


def build_clan_settings_embed(old: dict, new: dict, clan: dict) -> discord.Embed | None:
    """Embed « le clan a changé de réglage » ; None si rien de notable n'a changé."""
    lines: list[str] = []
    title = "⚙️ Réglages du clan modifiés"
    if old.get("type") != new.get("type") and new.get("type"):
        before = CLAN_TYPE_LABELS.get(old.get("type"), old.get("type") or "?")
        after = CLAN_TYPE_LABELS.get(new["type"], new["type"])
        title = f"{after.split(' ', 1)[0]} Le clan est maintenant {after.split(' ', 1)[1].lower()}"
        lines.append(f"**Type** : {before} → **{after}**")
    if old.get("requiredTrophies") != new.get("requiredTrophies") and new.get("requiredTrophies") is not None:
        before, after = old.get("requiredTrophies"), new["requiredTrophies"]
        arrow = "⬆️" if before is not None and after > before else "⬇️"
        lines.append(f"🏆 **Trophées requis** : {fmt_int(before) if before is not None else '?'} → **{fmt_int(after)}** {arrow}")
        if old.get("type") == new.get("type"):
            title = f"🏆 Trophées requis : {fmt_int(after)}"
    if old.get("name") != new.get("name"):
        lines.append(f"**Nom** : {esc(old.get('name') or '?')} → **{esc(new.get('name') or '?')}**")
    if old.get("description") != new.get("description"):
        lines.append(f"**Description** : {cap(esc(new.get('description') or '—'), 300)}")
    if old.get("location") != new.get("location"):
        lines.append(f"**Pays** : {old.get('location') or '?'} → **{new.get('location') or '?'}**")
    if not lines:
        return None
    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        color=CLAN_TYPE_COLORS.get(new.get("type"), discord.Color.blurple()),
        url=clan_url(clan["tag"]) if clan.get("tag") else None,
    )
    embed.add_field(name="État actuel", value=(
        f"{CLAN_TYPE_LABELS.get(new.get('type'), '?')} · {fmt_int(new.get('requiredTrophies') or 0)} 🏆 requis · "
        f"{clan.get('members', '?')}/50 membres"), inline=False)
    return embed


# ---------------------------------------------------------------------- guerre
def build_war_recap(race: dict, own_tag: str) -> discord.Embed | None:
    standing = next(
        (s for s in race.get("standings", []) if s.get("clan", {}).get("tag") == own_tag), None
    )
    if not standing:
        return None
    clan = standing["clan"]
    rank = standing.get("rank", "?")
    change = standing.get("trophyChange", 0)
    embed = discord.Embed(
        title=f"🏁 Guerre terminée — Saison {race.get('seasonId')} · Semaine {race.get('sectionIndex', 0) + 1}",
        color=discord.Color.gold() if rank == 1 else discord.Color.blurple(),
    )
    embed.add_field(
        name="Classement",
        value=f"**#{rank}** · {change:+d} 🏆 · {fmt_int(clan.get('fame', 0))} pts de gloire",
        inline=False,
    )
    parts = sorted(clan.get("participants", []), key=lambda p: p.get("fame", 0), reverse=True)
    top = [
        f"{MEDALS[i] if i < 3 else f'{i + 1}.'} {esc(p['name'])} — {fmt_int(p.get('fame', 0))} pts "
        f"({p.get('decksUsed', 0)}/16 decks)"
        for i, p in enumerate(parts[:5])
        if p.get("fame", 0) > 0
    ]
    embed.add_field(name="🏆 Top 5", value=cap("\n".join(top)) or "—", inline=False)
    lazy = [p for p in parts if p.get("decksUsed", 0) < 8]
    if lazy:
        lines = [f"• {esc(p['name'])} — {p.get('decksUsed', 0)}/16 decks" for p in lazy]
        embed.add_field(name="😴 Moins de 8 decks joués", value=cap("\n".join(lines)), inline=False)
    return embed


def build_decks_embed(rows: list[tuple[str, str, int]], title: str) -> discord.Embed:
    lines = [f"• {esc(name)} — **{4 - used}** deck{'s' if 4 - used > 1 else ''} restant{'s' if 4 - used > 1 else ''}"
             for name, _tag, used in rows]
    return discord.Embed(title=title, description=cap("\n".join(lines), 4000), color=discord.Color.orange())


def build_daily_report_embed(race: dict | None, clan_tag: str, decks_rows: list[tuple[str, str, int]]) -> discord.Embed:
    """Résumé quotidien posté dans le salon de guerre : période, classement, decks restants."""
    if race is None:
        return discord.Embed(
            title="📅 Nouveau jour", description="Le clan n'est pas engagé dans une course de guerre en ce moment.",
            color=discord.Color.light_grey(),
        )
    period = PERIOD_LABELS.get(race.get("periodType"), race.get("periodType", "?"))
    clans = sorted(race.get("clans", []), key=lambda c: c.get("fame", 0), reverse=True)
    own = next((c for c in clans if c.get("tag") == clan_tag), None)
    rank = clans.index(own) + 1 if own else None

    embed = discord.Embed(
        title=f"📅 Nouveau jour — {period}",
        color=discord.Color.orange() if race.get("periodType") == "warDay" else discord.Color.blurple(),
    )
    if own:
        embed.add_field(name="Classement du clan", value=f"**#{rank}**/{len(clans)} — {fmt_int(own.get('fame', 0))} pts", inline=True)
    if race.get("periodType") in ("warDay", "colosseum"):
        remaining = sum(4 - used for _n, _t, used in decks_rows)
        embed.add_field(name="Decks restants aujourd'hui", value=f"{remaining} ({len(decks_rows)} joueur(s) concerné(s))", inline=True)
        if decks_rows:
            lines = [f"• {esc(n)} — {4 - u} restant(s)" for n, _t, u in decks_rows[:10]]
            if len(decks_rows) > 10:
                lines.append(f"… et {len(decks_rows) - 10} de plus")
            embed.add_field(name="À relancer", value=cap("\n".join(lines)), inline=False)
        else:
            embed.add_field(name="À relancer", value="✅ Tout le monde a déjà joué ses decks.", inline=False)
    return embed


def since(value: str) -> timedelta:
    """Durée écoulée depuis un horodatage API (format 20240108T094412.000Z)."""
    from cr_api import parse_cr_time

    return datetime.now(timezone.utc) - parse_cr_time(value)
