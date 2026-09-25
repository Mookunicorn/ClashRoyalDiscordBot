"""Point d'entrée du bot Discord Clash Royale.

Seul DISCORD_TOKEN est nécessaire pour démarrer : la clé API Clash Royale, le clan, les salons
etc. se règlent ensuite depuis Discord avec /configurer (voir README).
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import Config
from cr_api import ClashRoyaleAPI
from database import Database
from discord_sync import RoleSync
from settings import Settings
from ui import LinkView

log = logging.getLogger("bot")

EXTENSIONS = (
    "cogs.tracker",
    "cogs.war",
    "cogs.commands",
    "cogs.discord_roles",
    "cogs.setup",
    "cogs.verification",
    "cogs.explorer",
    "cogs.updater",
)


class ClashBot(commands.Bot):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        # « Server Members Intent » (privilégié) : détecte les arrivées sur le Discord.
        # À activer dans le portail développeur, ou mettre MEMBERS_INTENT=false.
        intents.members = cfg.members_intent
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.cfg = cfg
        self.api = ClashRoyaleAPI()
        self.db = Database(cfg.db_path)
        self.sync = RoleSync(self)
        self.settings: Settings | None = None
        self.members: dict[str, dict] = {}   # cache alimenté par le tracker
        self.clan_info: dict = {}

    async def setup_hook(self) -> None:
        await self.api.start()
        await self.db.connect()
        self.settings = Settings(self)
        await self.settings.load()          # base de données > .env > défauts
        self.add_view(LinkView())
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        await self.tree.sync()
        if not self.cfg.ready:
            log.warning("Bot non configuré : utilise /configurer sur Discord.")

    async def on_ready(self) -> None:
        log.info("Connecté en tant que %s (clan %s)", self.user, self.cfg.clan_tag or "non configuré")

    async def close(self) -> None:
        await self.api.close()
        await self.db.close()
        await super().close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = Config.load()
    ClashBot(cfg).run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
