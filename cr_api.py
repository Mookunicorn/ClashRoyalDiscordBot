"""Client asynchrone pour l'API officielle Clash Royale (tous les endpoints documentés)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from yarl import URL

log = logging.getLogger("cr_api")

DEFAULT_BASE_URL = "https://api.clashroyale.com/v1"


class ClashAPIError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"HTTP {status} : {message}")
        self.status = status
        self.message = message


class NotFound(ClashAPIError):
    pass


class NotConfigured(ClashAPIError):
    def __init__(self):
        super().__init__(0, "clé API non configurée")


def parse_cr_time(value: str) -> datetime:
    """Format de l'API : 20240108T094412.000Z"""
    return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def encode_tag(tag: str) -> str:
    return quote(tag if tag.startswith("#") else f"#{tag}", safe="")


class ClashRoyaleAPI:
    def __init__(self, token: str | None = None, base_url: str = DEFAULT_BASE_URL, max_concurrency: int = 4):
        self._token = token
        self._base = base_url.rstrip("/")
        self._sem = asyncio.Semaphore(max_concurrency)
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ cycle de vie / réglages
    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"Accept": "application/json"}, timeout=aiohttp.ClientTimeout(total=20)
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def configure(self, token: str | None, base_url: str | None = None) -> None:
        """Change la clé / l'URL à chaud (utilisé par /configurer)."""
        self._token = token or None
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._cache.clear()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    # ------------------------------------------------------------------ bas niveau
    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise NotConfigured()
        return {"Authorization": f"Bearer {self._token}"}

    async def _get(self, path: str, params: dict | None = None, ttl: float = 0) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if params:
            path = f"{path}?{urlencode(params)}"
        if ttl:
            hit = self._cache.get(path)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        data = await self._request(path)
        if ttl:
            self._cache[path] = (time.monotonic() + ttl, data)
        return data

    async def get(self, path: str, params: dict | None = None, ttl: float = 0) -> Any:
        """Accès générique à n'importe quel endpoint (ex. '/events')."""
        return await self._get(path, params, ttl)

    async def _request(self, path: str) -> Any:
        assert self._session is not None, "API non démarrée (appeler start())"
        headers = self._headers()
        url = URL(f"{self._base}{path}", encoded=True)
        last_error: Exception | None = None
        for attempt in range(4):
            wait = 2 ** attempt
            try:
                async with self._sem:
                    async with self._session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            return await resp.json(content_type=None)
                        body = (await resp.text())[:300]
                        if resp.status == 404:
                            raise NotFound(404, body)
                        if resp.status in (401, 403):
                            raise ClashAPIError(resp.status, f"clé invalide ou IP non autorisée — {body}")
                        if resp.status in (429, 500, 502, 503, 504):
                            wait = float(resp.headers.get("Retry-After", wait))
                            last_error = ClashAPIError(resp.status, body)
                        else:
                            raise ClashAPIError(resp.status, body)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
            log.warning("Requête %s en échec (essai %d) : %s", path, attempt + 1, last_error)
            await asyncio.sleep(min(wait, 30))
        raise ClashAPIError(0, f"échec après plusieurs essais : {last_error}")

    @staticmethod
    def _items(data: Any) -> list:
        if isinstance(data, list):
            return data
        return data.get("items", []) if isinstance(data, dict) else []

    # ------------------------------------------------------------------ clans
    async def clan(self, tag: str, ttl: float = 0) -> dict:
        return await self._get(f"/clans/{encode_tag(tag)}", ttl=ttl)

    async def clan_members(self, tag: str, limit: int = 50, ttl: float = 60) -> list[dict]:
        return self._items(await self._get(f"/clans/{encode_tag(tag)}/members", {"limit": limit}, ttl))

    async def clan_search(self, name: str | None = None, location_id: int | None = None,
                          min_members: int | None = None, max_members: int | None = None,
                          min_score: int | None = None, limit: int = 10) -> list[dict]:
        params = {"name": name, "locationId": location_id, "minMembers": min_members,
                  "maxMembers": max_members, "minScore": min_score, "limit": limit}
        return self._items(await self._get("/clans", params, ttl=60))

    async def river_race_log(self, tag: str, limit: int = 30, ttl: float = 300) -> list[dict]:
        return self._items(await self._get(f"/clans/{encode_tag(tag)}/riverracelog", {"limit": limit}, ttl))

    async def current_river_race(self, tag: str, ttl: float = 30) -> dict:
        return await self._get(f"/clans/{encode_tag(tag)}/currentriverrace", ttl=ttl)

    async def war_log(self, tag: str, limit: int = 10, ttl: float = 300) -> list[dict]:
        """Ancien système de guerre (avant les Guerres de clans « River Race »)."""
        return self._items(await self._get(f"/clans/{encode_tag(tag)}/warlog", {"limit": limit}, ttl))

    async def current_war(self, tag: str, ttl: float = 30) -> dict:
        """Ancien système de guerre (avant les Guerres de clans « River Race »)."""
        return await self._get(f"/clans/{encode_tag(tag)}/currentwar", ttl=ttl)

    # ------------------------------------------------------------------ joueurs
    async def player(self, tag: str, ttl: float = 0) -> dict:
        return await self._get(f"/players/{encode_tag(tag)}", ttl=ttl)

    async def upcoming_chests(self, tag: str, ttl: float = 60) -> list[dict]:
        return self._items(await self._get(f"/players/{encode_tag(tag)}/upcomingchests", ttl=ttl))

    async def battle_log(self, tag: str, ttl: float = 120) -> list[dict]:
        return self._items(await self._get(f"/players/{encode_tag(tag)}/battlelog", ttl=ttl))

    async def verify_token(self, tag: str, token: str) -> str:
        """Vérifie le jeton API affiché dans les paramètres du jeu (POST /players/{tag}/verifytoken).

        Retourne : 'ok' | 'invalid' | 'notfound' | 'unavailable' (endpoint refusé pour cette clé).
        Le jeton n'est jamais journalisé.
        """
        assert self._session is not None, "API non démarrée (appeler start())"
        headers = self._headers()
        url = URL(f"{self._base}/players/{encode_tag(tag)}/verifytoken", encoded=True)
        try:
            async with self._sem:
                async with self._session.post(url, json={"token": token}, headers=headers) as resp:
                    status, body = resp.status, await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ClashAPIError(0, f"réseau ({type(exc).__name__})") from None
        if status == 400:
            return "invalid"
        if status == 404:
            return "notfound"
        if status in (401, 403):
            log.warning("verifytoken refusé (HTTP %d) : l'endpoint n'est pas accessible avec cette clé", status)
            return "unavailable"
        if status != 200:
            raise ClashAPIError(status, "verifytoken")
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        result = str(data.get("status", "")).lower() if isinstance(data, dict) else ""
        if result in ("ok", "valid"):
            return "ok"
        if result:
            return "invalid"
        # HTTP 200 sans champ « status » : réponse au format profil du joueur (invalide => 400)
        return "ok" if isinstance(data, dict) and data.get("tag") == tag else "invalid"

    # ------------------------------------------------------------------ cartes
    async def cards(self, ttl: float = 3600) -> dict:
        """{'items': [...cartes...], 'supportItems': [...troupes de tour...]}"""
        data = await self._get("/cards", ttl=ttl)
        return data if isinstance(data, dict) else {"items": data}

    # ------------------------------------------------------------------ tournois
    async def tournament_search(self, name: str, limit: int = 10, ttl: float = 60) -> list[dict]:
        return self._items(await self._get("/tournaments", {"name": name, "limit": limit}, ttl))

    async def tournament(self, tag: str, ttl: float = 30) -> dict:
        return await self._get(f"/tournaments/{encode_tag(tag)}", ttl=ttl)

    # ------------------------------------------------------------------ pays et classements
    async def locations(self, limit: int = 1000, ttl: float = 86400) -> list[dict]:
        return self._items(await self._get("/locations", {"limit": limit}, ttl))

    async def location(self, location_id: str | int, ttl: float = 86400) -> dict:
        return await self._get(f"/locations/{location_id}", ttl=ttl)

    async def ranking(self, kind: str, location: str | int = "global", limit: int = 25, ttl: float = 300) -> list[dict]:
        """kind : 'clans' | 'players' | 'clanwars'"""
        assert kind in ("clans", "players", "clanwars")
        return self._items(await self._get(f"/locations/{location}/rankings/{kind}", {"limit": limit}, ttl))

    async def pathoflegend_players(self, location: str | int = "global", limit: int = 25, ttl: float = 300) -> list[dict]:
        return self._items(await self._get(f"/locations/{location}/pathoflegend/players", {"limit": limit}, ttl))

    async def seasons(self, v2: bool = True, ttl: float = 3600) -> list[dict]:
        return self._items(await self._get("/locations/global/seasonsV2" if v2 else "/locations/global/seasons", ttl=ttl))

    async def season(self, season_id: str, ttl: float = 3600) -> dict:
        return await self._get(f"/locations/global/seasons/{quote(season_id, safe='')}", ttl=ttl)

    async def season_players(self, season_id: str, limit: int = 25, ttl: float = 3600) -> list[dict]:
        return self._items(await self._get(
            f"/locations/global/seasons/{quote(season_id, safe='')}/rankings/players", {"limit": limit}, ttl))

    async def pathoflegend_season_players(self, season_id: str, limit: int = 25, ttl: float = 3600) -> list[dict]:
        return self._items(await self._get(
            f"/locations/global/pathoflegend/{quote(season_id, safe='')}/rankings/players", {"limit": limit}, ttl))

    async def global_tournament_rankings(self, tag: str, limit: int = 25, ttl: float = 120) -> list[dict]:
        return self._items(await self._get(
            f"/locations/global/rankings/tournaments/{encode_tag(tag)}", {"limit": limit}, ttl))

    # ------------------------------------------------------------------ événements, classements spéciaux
    async def events(self, ttl: float = 300) -> list[dict]:
        return self._items(await self._get("/events", ttl=ttl))

    async def leaderboards(self, ttl: float = 3600) -> list[dict]:
        return self._items(await self._get("/leaderboards", ttl=ttl))

    async def leaderboard(self, leaderboard_id: str | int, limit: int = 25, ttl: float = 300) -> list[dict]:
        return self._items(await self._get(f"/leaderboard/{leaderboard_id}", {"limit": limit}, ttl))

    async def global_tournaments(self, ttl: float = 300) -> list[dict]:
        return self._items(await self._get("/globaltournaments", ttl=ttl))
