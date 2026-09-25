"""Mise à jour automatique du bot depuis GitHub (tâche périodique + /maj_verifier)."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ui import is_staff
from updater import apply_update, behind_count, is_git_repo

log = logging.getLogger("updater_cog")


class Updater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.check.change_interval(minutes=self.bot.cfg.update_interval)
        self.check.start()

    async def cog_unload(self) -> None:
        self.check.cancel()

    @tasks.loop(minutes=60)
    async def check(self) -> None:
        if not self.bot.cfg.auto_update_enabled:
            return
        try:
            count = await behind_count(self.bot)
            if count:
                log.info("%d nouveau(x) commit(s) détecté(s) sur GitHub : mise à jour…", count)
                await apply_update(self.bot)
        except Exception:
            log.exception("Erreur pendant la vérification de mise à jour")

    @check.before_loop
    async def _before_check(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_settings_changed(self, key: str) -> None:
        if key == "maj_intervalle":
            self.check.change_interval(minutes=self.bot.cfg.update_interval)

    @app_commands.command(name="maj_verifier", description="Vérifier et appliquer une mise à jour depuis GitHub maintenant")
    async def maj_verifier(self, interaction: discord.Interaction) -> None:
        if not is_staff(interaction):
            await interaction.response.send_message("Réservé au staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not is_git_repo():
            await interaction.followup.send(
                "⚠️ Le bot ne tourne pas depuis un dépôt Git (`git clone`) : la mise à jour automatique n'est pas disponible ici."
            )
            return
        count = await behind_count(self.bot)
        if count is None:
            await interaction.followup.send("❌ Impossible de vérifier les mises à jour (voir les logs du bot pour le détail).")
            return
        if count == 0:
            await interaction.followup.send("✅ Déjà à jour.")
            return
        await interaction.followup.send(
            f"🔄 {count} nouveau(x) commit(s) trouvé(s) sur GitHub : mise à jour en cours, le bot va redémarrer dans "
            "quelques secondes…"
        )
        await apply_update(self.bot)  # ne revient pas si tout se passe bien : le processus se relance ici


async def setup(bot) -> None:
    await bot.add_cog(Updater(bot))
