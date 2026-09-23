"""Historique d'un joueur : 5 derniers clans + moyenne des 5 dernières guerres.

L'API officielle n'expose ni l'historique de clans, ni l'historique de guerre d'un
joueur. On le reconstruit ainsi :
  1. Le journal de combats (25 derniers) indique, pour chaque combat, le clan dans
     lequel se trouvait le joueur -> on en déduit les clans récents (stockés en base).
  2. Pour chacun de ces clans, on lit le journal de guerre (riverracelog) et on y
     retrouve la participation du joueur (gloire + decks joués) -> on additionne les
     clans si le joueur a changé de clan pendant la même guerre.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cr_api import ClashAPIError, ClashRoyaleAPI, parse_cr_time
from database import Database

log = logging.getLogger("history")


@dataclass
class WarEntry:
    date: datetime
    season: int
    section: int
    fame: int = 0
    decks: int = 0
    clans: list[str] = field(default_factory=list)


@dataclass
class PlayerHistory:
    previous_clans: list[dict] = field(default_factory=list)
    wars: list[WarEntry] = field(default_factory=list)
    avg_fame: float | None = None
    avg_decks: float | None = None
    wars_in_own_clan: int = 0
    error: str | None = None


async def record_battlelog_clans(api: ClashRoyaleAPI, db: Database, player_tag: str) -> None:
    """Mémorise les clans vus dans le journal de combats du joueur."""
    try:
        battles = await api.battle_log(player_tag)
    except ClashAPIError as exc:
        log.warning("Journal de combats indisponible pour %s : %s", player_tag, exc)
        return
    seen: dict[str, tuple[str, datetime]] = {}
    for battle in battles:
        try:
            when = parse_cr_time(battle["battleTime"])
        except (KeyError, ValueError):
            continue
        for participant in battle.get("team", []):
            if participant.get("tag") != player_tag:
                continue
            clan = participant.get("clan") or {}
            if not clan.get("tag"):
                continue
            previous = seen.get(clan["tag"])
            if previous is None or when > previous[1]:
                seen[clan["tag"]] = (clan.get("name", clan["tag"]), when)
    for clan_tag, (name, when) in seen.items():
        await db.upsert_known_clan(player_tag, clan_tag, name, when.isoformat(timespec="seconds"))


async def _wars_in_clan(api: ClashRoyaleAPI, clan_tag: str, player_tag: str) -> list[WarEntry]:
    entries: list[WarEntry] = []
    for race in await api.river_race_log(clan_tag):
        for standing in race.get("standings", []):
            clan = standing.get("clan", {})
            if clan.get("tag") != clan_tag:
                continue
            for p in clan.get("participants", []):
                if p.get("tag") == player_tag:
                    try:
                        date = parse_cr_time(race["createdDate"])
                    except (KeyError, ValueError):
                        date = datetime.min.replace(tzinfo=timezone.utc)
                    entries.append(
                        WarEntry(
                            date=date,
                            season=race.get("seasonId", 0),
                            section=race.get("sectionIndex", 0),
                            fame=p.get("fame", 0),
                            decks=p.get("decksUsed", 0),
                            clans=[clan.get("name", clan_tag)],
                        )
                    )
    return entries


async def collect_wars(
    api: ClashRoyaleAPI, player_tag: str, clan_tags: list[str], own_clan_tag: str
) -> tuple[list[WarEntry], int]:
    results = await asyncio.gather(
        *(_wars_in_clan(api, ct, player_tag) for ct in clan_tags), return_exceptions=True
    )
    merged: dict[tuple[int, int], WarEntry] = {}
    own_count = 0
    for clan_tag, result in zip(clan_tags, results):
        if isinstance(result, Exception):
            log.warning("Journal de guerre indisponible pour %s : %s", clan_tag, result)
            continue
        if clan_tag == own_clan_tag:
            own_count = len(result)
        for entry in result:
            key = (entry.season, entry.section)
            if key in merged:  # même guerre jouée dans deux clans : on additionne
                merged[key].fame += entry.fame
                merged[key].decks += entry.decks
                merged[key].clans += entry.clans
            else:
                merged[key] = entry
    return sorted(merged.values(), key=lambda e: e.date, reverse=True), own_count


async def build_history(
    api: ClashRoyaleAPI,
    db: Database,
    player_tag: str,
    own_clan_tag: str,
    extra_clans: tuple = (),
    n_clans: int = 5,
    n_wars: int = 5,
) -> PlayerHistory:
    history = PlayerHistory()
    try:
        await record_battlelog_clans(api, db, player_tag)
        history.previous_clans = await db.known_clans(player_tag, exclude=own_clan_tag, limit=n_clans)
        clan_tags = [c["clan_tag"] for c in history.previous_clans]
        for tag in (own_clan_tag, *extra_clans):
            if tag and tag not in clan_tags:
                clan_tags.append(tag)
        wars, history.wars_in_own_clan = await collect_wars(api, player_tag, clan_tags, own_clan_tag)
        history.wars = wars[:n_wars]
        if history.wars:
            history.avg_fame = sum(w.fame for w in history.wars) / len(history.wars)
            history.avg_decks = sum(w.decks for w in history.wars) / len(history.wars)
    except Exception as exc:  # ne jamais bloquer une notification à cause de l'historique
        log.exception("Construction de l'historique impossible pour %s", player_tag)
        history.error = str(exc)
    return history
