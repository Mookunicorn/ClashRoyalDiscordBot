"""Réglages modifiables depuis Discord (/configurer).

Priorité : valeur enregistrée via /configurer  >  variable d'environnement (.env)  >  défaut.
La clé API est chiffrée en base (Fernet, clé dérivée de DISCORD_TOKEN ou de SECRET_KEY) : un
fichier de base de données copié seul ne révèle pas la clé.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken

from config import Config, normalize_tag

log = logging.getLogger("settings")

TAG_RE = re.compile(r"^#[0289PYLQGRJCUV]{3,15}$")
MODES = ("token", "carte", "clan")


class SettingError(ValueError):
    """Valeur invalide pour un réglage (message affichable à l'utilisateur)."""


@dataclass(frozen=True)
class Spec:
    key: str            # nom dans /configurer et en base
    attr: str           # attribut de Config
    env: str | None     # variable d'environnement de repli
    kind: str
    label: str
    secret: bool = False
    required: bool = False


SPECS: tuple[Spec, ...] = (
    Spec("cle_api", "cr_api_key", "CR_API_TOKEN", "secret", "Clé API Clash Royale", secret=True, required=True),
    Spec("clan", "clan_tag", "CLAN_TAG", "tag", "Clan suivi", required=True),
    Spec("salon_arrivees", "join_channel_id", "JOIN_CHANNEL_ID", "id", "Salon des arrivées", required=True),
    Spec("salon_departs", "leave_channel_raw", "LEAVE_CHANNEL_ID", "id", "Salon des départs (défaut : logs)"),
    Spec("salon_logs", "log_channel_raw", "LOG_CHANNEL_ID", "id", "Salon des logs (promotions, réglages du clan, liaisons)"),
    Spec("salon_guerre", "war_channel_raw", "WAR_CHANNEL_ID", "id", "Salon de guerre (rappels, récap)"),
    Spec("salon_accueil", "welcome_channel_id", "WELCOME_CHANNEL_ID", "id", "Salon d'accueil (si MP fermés)"),
    Spec("serveur", "guild_id", "GUILD_ID", "id", "Serveur Discord (pseudos / rôles)"),
    Spec("role_staff", "staff_role_id", "STAFF_ROLE_ID", "id", "Rôle staff"),
    Spec("intervalle", "poll_interval", "POLL_INTERVAL", "seconds", "Intervalle de surveillance du clan"),
    Spec("fuseau", "timezone", "TIMEZONE", "tz", "Fuseau horaire"),
    Spec("rappels", "reminder_times", "WAR_REMINDER_TIMES", "times", "Heures des rappels de guerre"),
    Spec("quotidien", "daily_report_enabled", "DAILY_REPORT_ENABLED", "bool", "Rapport quotidien dans le salon de guerre"),
    Spec("heure_quotidien", "daily_report_time", "DAILY_REPORT_TIME", "heure", "Heure du rapport quotidien"),
    Spec("liaison", "link_verification", "LINK_VERIFICATION", "mode", "Vérification de la liaison"),
    Spec("api_url", "api_base_url", "CR_API_BASE_URL", "url", "URL de l'API (proxy)"),
)
BY_KEY = {s.key: s for s in SPECS}


# ---------------------------------------------------------------------- validation
def parse(kind: str, raw: str):
    raw = raw.strip()
    if kind == "secret":
        raw = re.sub(r"\s+", "", raw)
        if len(raw) < 20:
            raise SettingError("Clé trop courte : colle la clé complète affichée sur developer.clashroyale.com.")
        return raw
    if kind == "tag":
        tag = normalize_tag(raw)
        if not TAG_RE.match(tag):
            raise SettingError("Tag invalide (caractères autorisés : 0289PYLQGRJCUV).")
        return tag
    if kind == "url":
        if not re.match(r"^https?://\S+$", raw):
            raise SettingError("URL invalide : elle doit commencer par http:// ou https://.")
        return raw.rstrip("/")
    if kind == "id":
        if not raw.isdigit():
            raise SettingError("Identifiant invalide (nombre attendu).")
        return int(raw)
    if kind == "seconds":
        if not raw.isdigit() or not 5 <= int(raw) <= 3600:
            raise SettingError("Intervalle invalide : entre 5 et 3600 secondes.")
        return int(raw)
    if kind == "tz":
        try:
            ZoneInfo(raw)
        except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError…
            raise SettingError("Fuseau inconnu. Exemples : Europe/Paris, America/Montreal, UTC.") from None
        return raw
    if kind == "times":
        if raw.lower() in ("", "none", "aucun"):
            return []
        times = []
        for chunk in raw.split(","):
            m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", chunk)
            if not m or int(m[1]) > 23 or int(m[2]) > 59:
                raise SettingError("Heures invalides. Format : `08:00,18:00` (ou `aucun`).")
            times.append(time(int(m[1]), int(m[2])))
        return times
    if kind == "mode":
        if raw.lower() not in MODES:
            raise SettingError("Mode invalide : `carte`, `token` ou `clan`.")
        return raw.lower()
    if kind == "bool":
        lowered = raw.lower()
        if lowered in ("oui", "actif", "active", "activé", "vrai", "true", "1", "on"):
            return True
        if lowered in ("non", "inactif", "inactive", "désactivé", "faux", "false", "0", "off"):
            return False
        raise SettingError("Valeur invalide : `oui` ou `non`.")
    if kind == "heure":
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", raw)
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            raise SettingError("Heure invalide. Format : `09:00`.")
        return time(int(m[1]), int(m[2]))
    raise SettingError(f"Type de réglage inconnu : {kind}")


def serialize(kind: str, value) -> str:
    if kind == "times":
        return ",".join(t.strftime("%H:%M") for t in value) or "none"
    if kind == "heure":
        return value.strftime("%H:%M")
    if kind == "bool":
        return "1" if value else "0"
    return str(value)


def display(spec: Spec, value) -> str:
    if value in (None, "", []):
        return "*non défini*"
    if spec.secret:
        return "🔒 définie"
    if spec.kind == "times":
        return ", ".join(t.strftime("%H:%M") for t in value)
    if spec.kind == "heure":
        return f"`{value.strftime('%H:%M')}`"
    if spec.kind == "bool":
        return "✅ Activé" if value else "❌ Désactivé"
    if spec.kind == "id" and spec.key.startswith("salon"):
        return f"<#{value}>"
    if spec.kind == "id" and spec.key == "role_staff":
        return f"<@&{value}>"
    if spec.kind == "seconds":
        return f"{value} s"
    return f"`{value}`"


def default_for(attr: str):
    for f in dataclasses.fields(Config):
        if f.name == attr:
            if f.default is not dataclasses.MISSING:
                return f.default
            if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                return f.default_factory()  # type: ignore[misc]
    return None


# ---------------------------------------------------------------------- chiffrement
class Vault:
    """Chiffre les secrets stockés en base (Fernet)."""

    PREFIX = "enc:"

    def __init__(self, material: str):
        digest = hashlib.sha256(("clash-bot-vault:" + material).encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self.PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, stored: str) -> str | None:
        if not stored.startswith(self.PREFIX):
            return stored
        try:
            return self._fernet.decrypt(stored[len(self.PREFIX):].encode()).decode()
        except InvalidToken:
            return None


# ---------------------------------------------------------------------- service
class Settings:
    def __init__(self, bot):
        self.bot = bot
        self.vault = Vault(os.getenv("SECRET_KEY") or bot.cfg.discord_token)

    async def load(self) -> None:
        cfg, db = self.bot.cfg, self.bot.db
        for spec in SPECS:
            raw = await db.get_setting(spec.key)
            if raw is not None and spec.secret and raw != "":
                decrypted = self.vault.decrypt(raw)
                if decrypted is None:
                    log.warning("Réglage %s illisible (DISCORD_TOKEN/SECRET_KEY modifié ?) : à ressaisir avec /configurer", spec.key)
                raw = decrypted
            if raw is None and spec.env:
                raw = os.getenv(spec.env, "").strip() or None   # valeur initiale depuis .env
            if not raw:                                          # absent, ou explicitement effacé
                continue
            try:
                setattr(cfg, spec.attr, parse(spec.kind, raw))
            except SettingError as exc:
                log.warning("Réglage %s invalide (%s) : ignoré", spec.key, exc)
        self.bot.api.configure(cfg.cr_api_key, cfg.api_base_url)

    async def set_value(self, spec: Spec, value) -> None:
        stored = serialize(spec.kind, value)
        await self.bot.db.set_setting(spec.key, self.vault.encrypt(stored) if spec.secret else stored)
        setattr(self.bot.cfg, spec.attr, value)
        self._after_change(spec)

    async def set(self, key: str, raw: str):
        spec = BY_KEY[key]
        value = parse(spec.kind, raw)
        await self.set_value(spec, value)
        return value

    async def clear(self, key: str) -> None:
        spec = BY_KEY[key]
        await self.bot.db.set_setting(spec.key, "")     # « effacé » : prime sur le .env
        setattr(self.bot.cfg, spec.attr, default_for(spec.attr))
        self._after_change(spec)

    def _after_change(self, spec: Spec) -> None:
        cfg = self.bot.cfg
        if spec.key in ("cle_api", "api_url"):
            self.bot.api.configure(cfg.cr_api_key, cfg.api_base_url)
        self.bot.dispatch("settings_changed", spec.key)

    def show(self, spec: Spec) -> str:
        return display(spec, getattr(self.bot.cfg, spec.attr))
