"""Rôles Discord liés aux rangs du clan, renommage automatique, accueil des nouveaux."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from discord_sync import ALL_KEY, RANK_KEYS, RANK_NAMES
from helpers import check_token_endpoint, handle_command_error, member_autocomplete, resolve_tag
from ui import LinkView, is_staff, link_panel_embed

log = logging.getLogger("roles")

RANK_CHOICES = [app_commands.Choice(name=RANK_NAMES[k], value=k) for k in (*RANK_KEYS, ALL_KEY)]


class DiscordRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_app_command_error(self, interaction, error):
        await handle_command_error(interaction, error)

    async def _staff_only(self, interaction: discord.Interaction) -> bool:
        if is_staff(interaction):
            return True
        await interaction.response.send_message("Réservé au staff.", ephemeral=True)
        return False

    # ------------------------------------------------------------------ accueil
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = self.bot.sync.guild()
        if member.bot or guild is None or member.guild.id != guild.id:
            return
        try:
            linked = await self.bot.db.find_by_discord(member.id)
            if linked:  # ancien membre qui revient : on réapplique pseudo + rôles
                await self.bot.sync.apply_safe(linked["tag"])
                return
            embed, view = link_panel_embed(), LinkView()
            try:
                await member.send(embed=embed, view=view)  # message privé (demandé en priorité)
            except discord.HTTPException:
                channel_id = self.bot.cfg.welcome_channel_id  # MP fermés : repli sur le salon d'accueil
                if channel_id:
                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                    await channel.send(f"👋 Bienvenue {member.mention} !", embed=embed, view=view)
        except discord.HTTPException:
            log.warning("Message d'accueil impossible pour %s (MP fermés ?)", member)

    # ------------------------------------------------------------------ configuration des rangs
    @app_commands.command(name="rang_config", description="Associer un rang du clan à un rôle Discord")
    @app_commands.describe(rang="Rang du clan", role="Rôle Discord à attribuer")
    @app_commands.choices(rang=RANK_CHOICES)
    async def rang_config(self, interaction: discord.Interaction, rang: str, role: discord.Role):
        if not await self._staff_only(interaction):
            return
        if role.is_default() or role.managed:
            await interaction.response.send_message("Ce rôle ne peut pas être attribué (@everyone ou rôle d'intégration).", ephemeral=True)
            return
        mapping = await self.bot.db.rank_roles()
        clash = next((k for k, rid in mapping.items() if rid == role.id and k != rang), None)
        if clash:
            await interaction.response.send_message(
                f"{role.mention} est déjà associé à « {RANK_NAMES[clash]} ». Un rôle par rang.",
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.set_rank_role(rang, role.id)
        text = f"✅ **{RANK_NAMES[rang]}** → {role.mention}"
        if not role.is_assignable():
            text += "\n⚠️ Je ne peux pas attribuer ce rôle : place le rôle du bot **au-dessus** dans Paramètres du serveur → Rôles."
        stats = await self.bot.sync.sync_all()
        text += f"\nSynchro : {stats['maj']} mis à jour, {stats['a_jour']} déjà à jour."
        await interaction.followup.send(text, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="rang_retirer", description="Supprimer l'association d'un rang avec son rôle Discord")
    @app_commands.choices(rang=RANK_CHOICES)
    async def rang_retirer(self, interaction: discord.Interaction, rang: str):
        if not await self._staff_only(interaction):
            return
        await self.bot.db.del_rank_role(rang)
        await interaction.response.send_message(
            f"🗑️ Association « {RANK_NAMES[rang]} » supprimée. Les rôles déjà attribués ne sont pas retirés.", ephemeral=True
        )

    @app_commands.command(name="rang_liste", description="Voir les rôles associés aux rangs et le réglage du renommage")
    async def rang_liste(self, interaction: discord.Interaction):
        if not await self._staff_only(interaction):
            return
        mapping = await self.bot.db.rank_roles()
        lines = [
            f"• **{RANK_NAMES[k]}** → " + (f"<@&{mapping[k]}>" if k in mapping else "*non configuré*")
            for k in (*RANK_KEYS, ALL_KEY)
        ]
        rename = await self.bot.sync.rename_enabled()
        lines.append("")
        lines.append(f"Renommage automatique : **{'activé' if rename else 'désactivé'}** (pseudo Discord = nom en jeu)")
        linked = len(await self.bot.db.linked_players())
        lines.append(f"Comptes liés : **{linked}** / {len(self.bot.members)} membres du clan")
        await interaction.response.send_message(
            embed=discord.Embed(title="🎭 Rangs → rôles Discord", description="\n".join(lines), color=discord.Color.blurple()),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="renommage", description="Activer / désactiver le renommage automatique (pseudo Discord = nom en jeu)")
    @app_commands.describe(actif="Activer ou désactiver")
    async def renommage(self, interaction: discord.Interaction, actif: bool):
        if not await self._staff_only(interaction):
            return
        await self.bot.db.set_meta("rename_enabled", "1" if actif else "0")
        await interaction.response.defer(ephemeral=True)
        text = f"Renommage **{'activé' if actif else 'désactivé'}** : le pseudo Discord des membres liés " + (
            "est le nom Clash Royale du joueur." if actif else "n'est plus modifié par le bot.")
        if actif:
            stats = await self.bot.sync.sync_all()
            text += f"\nSynchro : {stats['maj']} mis à jour."
        await interaction.followup.send(text)

    # ------------------------------------------------------------------ synchro / liaison
    @app_commands.command(name="sync", description="Forcer la synchronisation des pseudos et rôles maintenant")
    async def sync(self, interaction: discord.Interaction):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        stats = await self.bot.sync.sync_all()
        await interaction.followup.send(
            f"🔄 {stats['maj']} mis à jour · {stats['a_jour']} déjà à jour · "
            f"{stats['absents']} absents du Discord · {stats['avertissements']} avertissement(s)"
            + ("\n⚠️ Vérifie la hiérarchie des rôles et les permissions du bot." if stats["avertissements"] else "")
        )

    @app_commands.command(name="delier", description="Supprimer le lien entre un joueur et son compte Discord")
    @app_commands.autocomplete(joueur=member_autocomplete)
    async def delier(self, interaction: discord.Interaction, joueur: str):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        tag = await resolve_tag(self.bot, joueur)
        player = await self.bot.db.get_player(tag)
        if not player or not player.get("discord_id"):
            await interaction.followup.send("Ce joueur n'est lié à aucun compte Discord.")
            return
        try:
            await self.bot.sync.strip(player["discord_id"])
        except discord.HTTPException:
            log.warning("Retrait des rôles impossible pour %s", tag)
        await self.bot.db.set_discord_id(tag, None)
        await interaction.followup.send(f"🔓 `{tag}` n'est plus lié (rôles du bot retirés).")

    @app_commands.command(name="liaison_test", description="Tester si la vérification par jeton API fonctionne avec ta clé")
    async def liaison_test(self, interaction: discord.Interaction):
        if not await self._staff_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        level, text = await check_token_endpoint(self.bot)
        await interaction.followup.send({"ok": "✅ ", "warn": "⚠️ ", "error": "🚨 "}[level] + text)

    @app_commands.command(name="panneau", description="Poster ici le bouton « Lier mon compte Clash Royale »")
    async def panneau(self, interaction: discord.Interaction):
        if not await self._staff_only(interaction):
            return
        await interaction.channel.send(embed=link_panel_embed(), view=LinkView())
        await interaction.response.send_message("Panneau posté ✅", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(DiscordRoles(bot))
