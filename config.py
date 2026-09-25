"""Configuration.

Seul DISCORD_TOKEN est obligatoire au démarrage (le bot en a besoin pour se connecter).
Tout le reste (clé API, clan, salons…) se règle depuis Discord avec /configurer ; les variables
d'environnement (.env) ne servent que de valeurs initiales.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time

from dotenv import load_dotenv

load_dotenv()


def normalize_tag(tag: str) -> str:
    """'2pp', '#2pp' -> '#2PP' (le O est remplacé par 0, comme dans le jeu)."""
    tag = tag.strip().upper().replace("O", "0")
    return tag if tag.startswith("#") else f"#{tag}"


def _default_times() -> list[time]:
    return [time(8, 0), time(18, 0)]


@dataclass
class Config:
    # --- démarrage (variables d'environnement uniquement)
    discord_token: str
    db_path: str = "data/bot.db"
    members_intent: bool = True
    # --- réglables depuis Discord (/configurer)
    cr_api_key: str | None = None
    clan_tag: str | None = None
    api_base_url: str = "https://api.clashroyale.com/v1"
    join_channel_id: int | None = None
    leave_channel_raw: int | None = None
    log_channel_raw: int | None = None
    war_channel_raw: int | None = None
    welcome_channel_id: int | None = None
    guild_id: int | None = None
    staff_role_id: int | None = None
    poll_interval: int = 10
    timezone: str = "Europe/Paris"
    reminder_times: list[time] = field(default_factory=_default_times)
    link_verification: str = "token"
    auto_update_enabled: bool = False
    update_interval: int = 60
    daily_report_enabled: bool = False
    daily_report_time: time = time(9, 0)

    @property
    def log_channel_id(self) -> int | None:
        """Promotions, réglages du clan, liaisons (repli : salon des arrivées)."""
        return self.log_channel_raw or self.join_channel_id

    @property
    def leave_channel_id(self) -> int | None:
        """Départs (repli : salon des logs, puis salon des arrivées)."""
        return self.leave_channel_raw or self.log_channel_raw or self.join_channel_id

    @property
    def war_channel_id(self) -> int | None:
        return self.war_channel_raw or self.join_channel_id

    @property
    def ready(self) -> bool:
        """Configuration minimale pour suivre un clan."""
        return bool(self.cr_api_key and self.clan_tag)

    @classmethod
    def load(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("Variable manquante : DISCORD_TOKEN (voir .env.example)")
        return cls(
            discord_token=token,
            db_path=os.getenv("DB_PATH", "data/bot.db"),
            members_intent=os.getenv("MEMBERS_INTENT", "true").strip().lower() not in ("0", "false", "non", "no"),
        )
