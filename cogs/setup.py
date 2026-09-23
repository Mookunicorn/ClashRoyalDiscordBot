"""/configurer : une seule commande, tout le réglage se fait ensuite en message privé.

Tout passe par des formulaires Discord (modales) envoyés en MP à la personne qui lance la
commande : la clé API et les autres réglages ne s'affichent jamais dans un salon du
serveur. La commande capture le serveur d'où elle est lancée (pas besoin de le repréciser
en MP). Un menu déroulant permet d'aller directement à n'importe laquelle des 4 étapes,
dans n'importe quel ordre et autant de fois que voulu ; un bouton « suivant » reste
disponible pour le parcours linéaire habituel. Chaque champ est facultatif et préaffiche
la valeur actuelle — le laisser vide revient à ne pas y toucher.
"""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cr_api import ClashAPIError
from embeds import esc
from helpers import build_diagnostic_embed, key_hint, resolve_channel, resolve_role
from settings import BY_KEY, SettingError, parse

log = logging.getLogger("setup")

# user_id -> (guild_id, expiration) : le serveur d'où /configurer a été lancé, le temps de l'assistant
_GUILDS: dict[int, tuple[int, float]] = {}
WIZARD_TTL = 900  # 15 minutes


def start_wizard(user_id: int, guild_id: int) -> None:
    _GUILDS[user_id] = (guild_id, time.monotonic() + WIZARD_TTL)


def wizard_guild(bot, user_id: int) -> discord.Guild | None:
    entry = _GUILDS.get(user_id)
    if not entry or entry[1] < time.monotonic():
        _GUILDS.pop(user_id, None)
        return None
    return bot.get_guild(entry[0])


def end_wizard(user_id: int) -> None:
    _GUILDS.pop(user_id, None)


def touch_wizard(user_id: int) -> None:
    """Prolonge la session en cours (si elle existe) à chaque interaction de l'assistant."""
    entry = _GUILDS.get(user_id)
    if entry:
        _GUILDS[user_id] = (entry[0], time.monotonic() + WIZARD_TTL)


def current_or_empty(value) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, list):  # heures de rappel
        return ",".join(t.strftime("%H:%M") for t in value)
    return str(value)


async def apply_field(bot, results: list[str], key: str, raw: str) -> None:
    """Valide et enregistre un champ non vide ; consigne le résultat dans `results`."""
    spec = BY_KEY[key]
    if not raw.strip():
        return
    try:
        await bot.settings.set(key, raw)
    except SettingError as exc:
        results.append(f"❌ {spec.label} : {exc}")
        return
    results.append(f"✅ {spec.label} : {bot.settings.show(spec)}")


async def apply_channel_field(bot, guild, results: list[str], key: str, raw: str) -> None:
    spec = BY_KEY[key]
    if not raw.strip():
        return
    channel = resolve_channel(guild, raw)
    if channel is None:
        results.append(f"❌ {spec.label} : salon `{raw}` introuvable sur ce serveur.")
        return
    await apply_field(bot, results, key, str(channel.id))


async def apply_role_field(bot, guild, results: list[str], key: str, raw: str) -> None:
    spec = BY_KEY[key]
    if not raw.strip():
        return
    if raw.strip().lower() in ("aucun", "none", "-"):
        await bot.settings.clear(key)
        results.append(f"🗑️ {spec.label} effacé.")
        return
    role = resolve_role(guild, raw)
    if role is None:
        results.append(f"❌ {spec.label} : rôle `{raw}` introuvable sur ce serveur.")
        return
    await apply_field(bot, results, key, str(role.id))


STEP_LABELS = {
    "1": "1. Clé API & clan",
    "2": "2. Salons",
    "3": "3. Rôles & surveillance",
    "4": "4. Guerre & liaison",
}
STEP_EMOJIS = {"1": "🔑", "2": "📢", "3": "🛡️", "4": "⚔️"}


class StepSelect(discord.ui.Select):
    """Menu déroulant pour aller directement à n'importe quelle étape, dans n'importe quel ordre."""

    def __init__(self, bot, user_id: int, current: str | None = None):
        options = [
            discord.SelectOption(label=label, value=key, emoji=STEP_EMOJIS[key], default=(key == current))
            for key, label in STEP_LABELS.items()
        ]
        super().__init__(placeholder="↕️ Aller directement à une étape…", options=options, min_values=1, max_values=1)
        self.bot, self.user_id = bot, user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        touch_wizard(self.user_id)
        step_cls = {"1": Step1, "2": Step2, "3": Step3, "4": Step4}[self.values[0]]
        await interaction.response.send_modal(step_cls(self.bot))


class WizardView(discord.ui.View):
    """Vue affichée à chaque étape : menu pour sauter à une autre étape, bouton « suivant »
    (facultatif, pour le parcours linéaire) et bouton de test — toujours accessibles ensemble."""

    def __init__(self, bot, user_id: int, current: str | None = None, next_label: str | None = None, next_factory=None):
        super().__init__(timeout=WIZARD_TTL)
        self.bot, self.user_id = bot, user_id
        self.add_item(StepSelect(bot, user_id, current))
        if next_factory is not None:
            next_button = discord.ui.Button(label=next_label, emoji="➡️", style=discord.ButtonStyle.primary)
            next_button.callback = self._make_next(next_factory)
            self.add_item(next_button)
        test_button = discord.ui.Button(label="Tester la configuration", emoji="🩺", style=discord.ButtonStyle.secondary)
        test_button.callback = self._test
        self.add_item(test_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    def _make_next(self, factory):
        async def callback(interaction: discord.Interaction) -> None:
            touch_wizard(self.user_id)
            await interaction.response.send_modal(factory(self.bot))
        return callback

    async def _test(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        touch_wizard(self.user_id)
        guild = wizard_guild(self.bot, self.user_id) or (self.bot.get_guild(self.bot.cfg.guild_id) if self.bot.cfg.guild_id else None)
        await interaction.followup.send(embed=await build_diagnostic_embed(self.bot, guild), ephemeral=True)


# ---------------------------------------------------------------------- étape 1 : clé API + clan
class Step1(discord.ui.Modal, title="Configuration 1/4 — Clé API & clan"):
    def __init__(self, bot):
        super().__init__()
        self.cle = discord.ui.TextInput(
            label="Clé API Clash Royale", style=discord.TextStyle.paragraph, required=False,
            placeholder="🔒 déjà définie — laisse vide pour la garder" if bot.cfg.cr_api_key else "developer.clashroyale.com → My Account",
        )
        self.clan = discord.ui.TextInput(
            label="Tag du clan à suivre", required=False, placeholder="#XXXXXXXX",
            default=current_or_empty(bot.cfg.clan_tag),
        )
        self.add_item(self.cle)
        self.add_item(self.clan)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=False)
        touch_wizard(interaction.user.id)
        results: list[str] = []

        if self.cle.value.strip():
            try:
                key = parse("secret", self.cle.value)
            except SettingError as exc:
                results.append(f"❌ Clé API : {exc}")
            else:
                old = bot.cfg.cr_api_key
                bot.api.configure(key, bot.cfg.api_base_url)   # test avant d'enregistrer
                try:
                    await bot.api.get("/locations", {"limit": 1})
                except ClashAPIError as exc:
                    bot.api.configure(old, bot.cfg.api_base_url)
                    results.append(f"❌ Clé API refusée par l'API ({exc.status}).{key_hint(exc.status)}")
                else:
                    await bot.settings.set_value(BY_KEY["cle_api"], key)
                    results.append("✅ Clé API enregistrée (chiffrée) et testée avec succès.")

        if self.clan.value.strip():
            try:
                tag = parse("tag", self.clan.value)
            except SettingError as exc:
                results.append(f"❌ Clan : {exc}")
                tag = None
            if tag is not None:
                if not bot.cfg.cr_api_key:
                    results.append("❌ Clan : renseigne d'abord une clé API valide (champ du dessus).")
                else:
                    try:
                        info = await bot.api.clan(tag)
                    except ClashAPIError as exc:
                        results.append(f"❌ Clan `{tag}` inaccessible ({exc.status}).{key_hint(exc.status)}")
                    else:
                        changed_clan = bot.cfg.clan_tag and bot.cfg.clan_tag != tag
                        if changed_clan and await bot.db.has_tracking():
                            await bot.db.reset_tracking()
                            bot.members = {}
                            results.append("♻️ Changement de clan : le suivi (passages, récap de guerre) a été remis à zéro.")
                        await bot.settings.set_value(BY_KEY["clan"], tag)
                        results.append(f"✅ Clan suivi : **{esc(info.get('name', '?'))}** (`{tag}`, {info.get('members', '?')}/50 membres).")

        if not results:
            results.append("(rien de changé)")
        await send_step(interaction, "Étape 1/4 — Clé API & clan", results, "1", "Étape 2/4 : Salons →", Step2)


# ---------------------------------------------------------------------- étape 2 : salons
class Step2(discord.ui.Modal, title="Configuration 2/4 — Salons"):
    def __init__(self, bot):
        super().__init__()
        cfg = bot.cfg
        specs = [("arrivees", "salon_arrivees", "Arrivées de joueurs"),
                 ("departs", "salon_departs", "Départs (défaut : logs)"),
                 ("logs", "salon_logs", "Logs : promotions, réglages, liaisons"),
                 ("guerre", "salon_guerre", "Guerre : rappels & récap"),
                 ("accueil", "salon_accueil", "Accueil (si MP fermés)")]
        self.fields_map = {}
        for attr_name, key, label in specs:
            cid = getattr(cfg, BY_KEY[key].attr)
            field = discord.ui.TextInput(
                label=label[:45], required=False,
                placeholder="Nom, mention ou identifiant du salon",
                default=f"<#{cid}>" if cid else "",
            )
            self.fields_map[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=False)
        touch_wizard(interaction.user.id)
        guild = wizard_guild(bot, interaction.user.id)
        results: list[str] = []
        if guild is None:
            results.append("❌ Serveur introuvable : relance `/configurer` depuis ton serveur.")
        else:
            for key, field in self.fields_map.items():
                await apply_channel_field(bot, guild, results, key, field.value)
        if not results:
            results.append("(rien de changé)")
        await send_step(interaction, "Étape 2/4 — Salons", results, "2", "Étape 3/4 : Rôles & surveillance →", Step3)


# ---------------------------------------------------------------------- étape 3 : rôle staff, surveillance, fuseau
class Step3(discord.ui.Modal, title="Configuration 3/4 — Rôles & surveillance"):
    def __init__(self, bot):
        super().__init__()
        cfg = bot.cfg
        self.staff = discord.ui.TextInput(
            label="Rôle staff (« aucun » pour l'effacer)", required=False,
            placeholder="Nom, mention ou identifiant du rôle",
            default=f"<@&{cfg.staff_role_id}>" if cfg.staff_role_id else "",
        )
        self.surveillance = discord.ui.TextInput(
            label="Surveillance du clan (secondes, 5-3600)", required=False,
            default=str(cfg.poll_interval),
        )
        self.fuseau = discord.ui.TextInput(
            label="Fuseau horaire", required=False,
            placeholder="Europe/Paris", default=cfg.timezone,
        )
        self.add_item(self.staff)
        self.add_item(self.surveillance)
        self.add_item(self.fuseau)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=False)
        touch_wizard(interaction.user.id)
        guild = wizard_guild(bot, interaction.user.id)
        results: list[str] = []
        if guild is None:
            results.append("❌ Serveur introuvable : relance `/configurer` depuis ton serveur.")
        else:
            if bot.cfg.guild_id != guild.id:
                await bot.settings.set_value(BY_KEY["serveur"], guild.id)
                results.append(f"✅ Serveur : **{esc(guild.name)}**")
            await apply_role_field(bot, guild, results, "role_staff", self.staff.value)
        await apply_field(bot, results, "intervalle", self.surveillance.value)
        await apply_field(bot, results, "fuseau", self.fuseau.value)
        if not results:
            results.append("(rien de changé)")
        await send_step(interaction, "Étape 3/4 — Rôles & surveillance", results, "3", "Étape 4/4 : Guerre & liaison →", Step4)


# ---------------------------------------------------------------------- étape 4 : rappels, liaison, url api
class Step4(discord.ui.Modal, title="Configuration 4/4 — Guerre & liaison"):
    def __init__(self, bot):
        super().__init__()
        cfg = bot.cfg
        self.rappels = discord.ui.TextInput(
            label="Heures des rappels de decks", required=False,
            placeholder="08:00,18:00 (ou « aucun »)", default=current_or_empty(cfg.reminder_times),
        )
        self.quotidien = discord.ui.TextInput(
            label="Rapport quotidien dans le salon de guerre", required=False,
            placeholder="oui ou non", default="oui" if cfg.daily_report_enabled else "non",
        )
        self.heure_quotidien = discord.ui.TextInput(
            label="Heure du rapport quotidien", required=False,
            placeholder="09:00", default=cfg.daily_report_time.strftime("%H:%M"),
        )
        self.liaison = discord.ui.TextInput(
            label="Vérification de liaison : token / carte / clan", required=False,
            placeholder="token (recommandé, plus simple)", default=cfg.link_verification,
        )
        self.api_url = discord.ui.TextInput(
            label="URL de l'API (« defaut » pour réinitialiser)", required=False,
            default=cfg.api_base_url,
        )
        self.add_item(self.rappels)
        self.add_item(self.quotidien)
        self.add_item(self.heure_quotidien)
        self.add_item(self.liaison)
        self.add_item(self.api_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=False)
        touch_wizard(interaction.user.id)
        results: list[str] = []
        await apply_field(bot, results, "rappels", self.rappels.value)
        await apply_field(bot, results, "quotidien", self.quotidien.value)
        await apply_field(bot, results, "heure_quotidien", self.heure_quotidien.value)
        await apply_field(bot, results, "liaison", self.liaison.value)
        if self.api_url.value.strip().lower() in ("defaut", "défaut", "default"):
            await bot.settings.clear("api_url")
            results.append("✅ URL de l'API : valeur par défaut.")
        else:
            await apply_field(bot, results, "api_url", self.api_url.value)
        if not results:
            results.append("(rien de changé)")

        embed = discord.Embed(title="Étape 4/4 — Guerre & liaison", description="\n".join(results), color=discord.Color.blurple())
        await interaction.followup.send(embed=embed)

        done = discord.Embed(
            title="✅ Configuration à jour",
            description=(
                "Choisis une autre étape dans le menu ci-dessous pour continuer à régler le bot, ou "
                "teste la configuration. Cette session reste ouverte 15 minutes après ta dernière action ; "
                "passé ce délai, relance simplement `/configurer`."
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=done, view=WizardView(bot, interaction.user.id, current="4"))


async def send_step(interaction: discord.Interaction, title: str, results: list[str], current: str,
                    next_label: str, factory) -> None:
    embed = discord.Embed(title=title, description="\n".join(results), color=discord.Color.blurple())
    bot = interaction.client
    view = WizardView(bot, interaction.user.id, current=current, next_label=next_label, next_factory=factory)
    await interaction.followup.send(embed=embed, view=view)


# ---------------------------------------------------------------------- point d'entrée
class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="configurer", description="Configurer le bot (clé API, clan, salons…) en message privé")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def configurer(self, interaction: discord.Interaction) -> None:
        perms = interaction.user.guild_permissions
        if not (perms.administrator or perms.manage_guild):
            await interaction.response.send_message(
                "Réservé aux administrateurs du serveur (permission « Gérer le serveur »).", ephemeral=True
            )
            return
        start_wizard(interaction.user.id, interaction.guild.id)
        embed = discord.Embed(
            title="⚙️ Configuration du bot",
            description=(
                f"Serveur : **{esc(interaction.guild.name)}**\n\n"
                "Rien de ce que tu saisis ici (clé API comprise) n'apparaît dans un salon du serveur : "
                "tout se passe dans ce message privé. Choisis une étape dans le menu ci-dessous — dans "
                "l'ordre que tu veux, et autant de fois que tu veux. Chaque champ affiche sa valeur "
                "actuelle et peut être laissé vide pour ne pas y toucher."
            ),
            color=discord.Color.blurple(),
        )
        try:
            dm = await interaction.user.create_dm()
            await dm.send(embed=embed, view=WizardView(self.bot, interaction.user.id))
        except discord.HTTPException:
            end_wizard(interaction.user.id)
            await interaction.response.send_message(
                "❌ Je n'arrive pas à t'écrire en message privé. Autorise les messages privés des membres du "
                "serveur (Paramètres de confidentialité Discord) puis relance `/configurer`.", ephemeral=True
            )
            return
        await interaction.response.send_message("📬 Je t'ai envoyé la configuration en message privé.", ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Setup(bot))
