"""Fonctions partagées pour la guerre en cours."""
from __future__ import annotations

from cr_api import ClashRoyaleAPI


async def decks_remaining(api: ClashRoyaleAPI, clan_tag: str):
    """Retourne (course, guerre_en_cours, [(nom, tag, decks_joues_aujourd_hui)]) triés."""
    race = await api.current_river_race(clan_tag, ttl=20)
    clan = await api.clan(clan_tag, ttl=20)
    members = {m["tag"]: m["name"] for m in clan.get("memberList", [])}
    participants = {p["tag"]: p for p in race.get("clan", {}).get("participants", [])}
    rows = [(name, tag, participants.get(tag, {}).get("decksUsedToday", 0)) for tag, name in members.items()]
    war_day = race.get("periodType") in ("warDay", "colosseum")
    return race, war_day, sorted((r for r in rows if r[2] < 4), key=lambda r: r[2])
