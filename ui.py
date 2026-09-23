"""Composants d'interface : contrôle des droits et liaison Discord <-> Clash Royale."""
from __future__ import annotations

import logging
import re
import secrets
import time

import discord

from config import normalize_tag
from cr_api import ClashAPIError, NotFound
from embeds import esc

log = logging.getLogger("ui")


def is_staff(interaction: discord.Interaction) -> bool:
    cfg = interaction.client.cfg
    user = interaction.user
    perms = getattr(user, "guild_permissions", None)
    if perms and (perms.manage_guild or perms.administrator):
        return True
    if cfg.staff_role_id and isinstance(user, discord.Member):
        return any(r.id == cfg.staff_role_id for r in user.roles)
    return False


async def can_edit(interaction: discord.Interaction, tag: str) -> bool:
    if is_staff(interaction):
        return True
    player = await interaction.client.db.get_player(tag)
    return bool(player and player.get("discord_id") == interaction.user.id)


# ---------------------------------------------------------------------- liaison Discord <-> Clash Royale
# Parcours en 2 étapes (formulaires) :
#   1. le joueur saisit son tag  -> le bot vérifie que le tag est correct et dans le clan
#   2. il saisit son jeton API   -> le bot le fait vérifier par Supercell (preuve de propriété)
# Le jeton est saisi dans un formulaire : il ne reste dans aucun message de la conversation.
TAG_RE = re.compile(r"^#[0289PYLQGRJCUV]{3,15}$")
MAX_FAILS, FAIL_WINDOW = 5, 600          # 5 jetons erronés max par tranche de 10 minutes
_FAILS: dict[int, list[float]] = {}
CHALLENGE_TTL, MAX_CARD_CHECKS = 900, 15   # défi « carte favorite » : 15 min, 15 vérifications
_CHALLENGES: dict[int, dict] = {}           # user_id -> {"tag", "card", "icon", "expires", "checks"}


def _recent_fails(user_id: int) -> list[float]:
    now = time.monotonic()
    fails = [t for t in _FAILS.get(user_id, []) if now - t < FAIL_WINDOW]
    _FAILS[user_id] = fails
    return fails


async def check_link_eligibility(bot, tag: str, user_id: int) -> tuple[dict | None, str | None]:
    """Retourne (infos du membre du clan, None) ou (None, message d'erreur)."""
    if not TAG_RE.match(tag):
        return None, f"Tag incorrect : `{tag}` n'a pas un format valide (caractères autorisés : 0289PYLQGRJCUV)."
    info = bot.members.get(tag)
    if info is None:
        try:
            profile = await bot.api.player(tag)
        except NotFound:
            return None, f"Tag incorrect : aucun joueur ne correspond à `{tag}`."
        except ClashAPIError:
            return None, "L'API Clash Royale ne répond pas, réessaie dans un instant."
        return None, (
            f"Le tag `{tag}` existe (**{esc(profile.get('name', '?'))}**) mais ce joueur n'est pas dans le clan "
            "pour le moment. Si tu viens de rejoindre, réessaie dans 1–2 minutes."
        )
    existing = await bot.db.get_player(tag)
    if existing and existing.get("discord_id") and existing["discord_id"] != user_id:
        return None, "Ce joueur est déjà lié à un autre compte Discord. Si c'est une erreur, préviens le staff."
    mine = await bot.db.find_by_discord(user_id)
    if mine and mine["tag"] != tag:
        return None, f"Ton compte Discord est déjà lié à `{mine['tag']}`. Demande au staff de le délier si besoin."
    return info, None


async def finalize_link(bot, interaction: discord.Interaction, tag: str, info: dict, verified: bool,
                        method: str = "jeton API") -> None:
    await bot.db.set_discord_id(tag, interaction.user.id, info["name"], verified=verified)
    _FAILS.pop(interaction.user.id, None)
    _CHALLENGES.pop(interaction.user.id, None)
    report = await bot.sync.apply_safe(tag)
    await interaction.followup.send(
        f"✅ Compte lié à **{esc(info['name'])}** (`{tag}`).\n" + (report.summary() if report else ""),
        ephemeral=True,
    )
    # trace pour le staff
    try:
        channel_id = bot.cfg.log_channel_id
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        proof = f"identité vérifiée ({method}) ✅" if verified else "⚠️ sans vérification d'identité"
        await channel.send(
            f"🔗 {interaction.user.mention} s'est lié au joueur **{esc(info['name'])}** (`{tag}`) — {proof}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.HTTPException, TypeError, AttributeError):
        log.warning("Trace de liaison non envoyée")


class TokenModal(discord.ui.Modal, title="Étape 2/2 : jeton API"):
    jeton = discord.ui.TextInput(label="Jeton API (paramètres du jeu)", min_length=4, max_length=128)

    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        if len(_recent_fails(user_id)) >= MAX_FAILS:
            await interaction.followup.send("Trop d'essais incorrects. Réessaie dans quelques minutes.", ephemeral=True)
            return
        info, error = await check_link_eligibility(bot, self.tag, user_id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        try:
            result = await bot.api.verify_token(self.tag, self.jeton.value.strip())
        except ClashAPIError:
            await interaction.followup.send("L'API Clash Royale ne répond pas, réessaie dans un instant.", ephemeral=True)
            return
        if result == "ok":
            await finalize_link(bot, interaction, self.tag, info, verified=True)
        elif result == "invalid":
            _FAILS[user_id] = _recent_fails(user_id) + [time.monotonic()]
            left = MAX_FAILS - len(_FAILS[user_id])
            await interaction.followup.send(
                f"❌ Jeton incorrect ou expiré. Vérifie que tu l'as copié en entier depuis le jeu "
                f"(ouvre de nouveau le formulaire avec le bouton). Essais restants : {left}.",
                ephemeral=True,
            )
        elif result == "notfound":
            await interaction.followup.send(f"Tag incorrect : aucun joueur ne correspond à `{self.tag}`.", ephemeral=True)
        else:
            await interaction.followup.send(
                "La vérification par jeton n'est pas disponible pour le moment. Préviens le staff : "
                "il peut te lier avec `/lier`.",
                ephemeral=True,
            )


class TokenView(discord.ui.View):
    """Bouton de l'étape 2 (éphémère, réservé au joueur qui a passé l'étape 1)."""

    def __init__(self, tag: str, user_id: int):
        super().__init__(timeout=600)
        self.tag, self.user_id = tag, user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce bouton ne t'est pas destiné.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Saisir mon jeton API", emoji="🔑", style=discord.ButtonStyle.primary)
    async def enter_token(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TokenModal(self.tag))


async def start_card_challenge(interaction: discord.Interaction, tag: str, info: dict) -> None:
    """Preuve de propriété sans accès spécial : le joueur doit choisir une carte favorite imposée."""
    bot = interaction.client
    try:
        cards = [c for c in (await bot.api.cards()).get("items", []) if c.get("name")]
        profile = await bot.api.player(tag)
    except ClashAPIError:
        await interaction.followup.send("L'API Clash Royale ne répond pas, réessaie dans un instant.", ephemeral=True)
        return
    current = (profile.get("currentFavouriteCard") or {}).get("name")
    choices = [c for c in cards if c["name"] != current]      # jamais la carte déjà choisie
    if not choices:
        await interaction.followup.send("Impossible de générer le défi pour le moment.", ephemeral=True)
        return
    card = secrets.choice(choices)
    icon = (card.get("iconUrls") or {}).get("medium")
    _CHALLENGES[interaction.user.id] = {
        "tag": tag, "card": card["name"], "expires": time.monotonic() + CHALLENGE_TTL, "checks": 0,
    }
    embed = discord.Embed(
        title=f"Étape 2/2 : choisis la carte favorite « {card['name']} »",
        description=(
            f"✅ Tag correct : **{esc(info['name'])}** (`{tag}`), membre du clan.\n\n"
            "Pour prouver que ce compte est le tien, change ta **carte favorite** dans Clash Royale :\n"
            "1. ouvre ton **profil** (ton nom en haut à gauche) ;\n"
            "2. touche la **carte favorite** et choisis "
            f"**{card['name']}** (le nom peut être traduit dans ton jeu, regarde l'image) ;\n"
            "3. reviens ici et clique sur **Vérifier** (la mise à jour peut prendre ~1 minute).\n\n"
            "Tu pourras remettre ta carte favorite d'origine ensuite. Le défi expire dans 15 minutes."
        ),
        color=discord.Color.blurple(),
    )
    if icon:
        embed.set_thumbnail(url=icon)
    await interaction.followup.send(embed=embed, view=CardChallengeView(tag, interaction.user.id), ephemeral=True)


class CardChallengeView(discord.ui.View):
    """Bouton « Vérifier » du défi carte favorite (éphémère, réservé à son propriétaire)."""

    def __init__(self, tag: str, user_id: int):
        super().__init__(timeout=CHALLENGE_TTL)
        self.tag, self.user_id = tag, user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce bouton ne t'est pas destiné.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Vérifier", emoji="✅", style=discord.ButtonStyle.success)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=True)
        challenge = _CHALLENGES.get(self.user_id)
        if not challenge or challenge["tag"] != self.tag or challenge["expires"] < time.monotonic():
            _CHALLENGES.pop(self.user_id, None)
            await interaction.followup.send("Le défi a expiré : recommence avec le bouton « Lier mon compte ».", ephemeral=True)
            return
        challenge["checks"] += 1
        if challenge["checks"] > MAX_CARD_CHECKS:
            _CHALLENGES.pop(self.user_id, None)
            await interaction.followup.send("Trop de vérifications : recommence avec le bouton « Lier mon compte ».", ephemeral=True)
            return
        info, error = await check_link_eligibility(bot, self.tag, self.user_id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        try:
            profile = await bot.api.player(self.tag)      # sans cache : on veut l'état actuel
        except ClashAPIError:
            await interaction.followup.send("L'API Clash Royale ne répond pas, réessaie dans un instant.", ephemeral=True)
            return
        favourite = (profile.get("currentFavouriteCard") or {}).get("name") or "aucune"
        if favourite.lower() == challenge["card"].lower():
            await finalize_link(bot, interaction, self.tag, info, verified=True, method="carte favorite")
        else:
            await interaction.followup.send(
                f"Pas encore : ta carte favorite est actuellement **{esc(favourite)}**, il faut "
                f"**{esc(challenge['card'])}**. Change-la dans ton profil, patiente ~1 minute puis reclique sur Vérifier.",
                ephemeral=True,
            )


class LinkModal(discord.ui.Modal, title="Étape 1/2 : ton tag"):
    tag = discord.ui.TextInput(label="Ton tag Clash Royale", placeholder="#2PP0YQ", min_length=3, max_length=15)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        await interaction.response.defer(ephemeral=True)
        tag = normalize_tag(self.tag.value)
        info, error = await check_link_eligibility(bot, tag, interaction.user.id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        mode = bot.cfg.link_verification
        if mode == "token":
            await interaction.followup.send(
                f"✅ Tag correct : **{esc(info['name'])}** (`{tag}`), membre du clan.\n\n"
                "**Étape 2/2 — confirme que ce compte est bien le tien.** Ouvre Clash Royale, va dans les "
                "paramètres et copie ton **jeton API**, puis clique sur le bouton ci-dessous et colle-le. "
                "Il est vérifié par Supercell et n'est conservé nulle part.",
                view=TokenView(tag, interaction.user.id),
                ephemeral=True,
            )
        elif mode == "carte":
            await start_card_challenge(interaction, tag, info)
        else:
            await finalize_link(bot, interaction, tag, info, verified=False)


class LinkView(discord.ui.View):
    """Bouton persistant « Lier mon compte » (custom_id fixe : survit aux redémarrages)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Lier mon compte Clash Royale", emoji="🔗", style=discord.ButtonStyle.success, custom_id="cr:link")
    async def link(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(LinkModal())


def link_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="🔗 Lie ton compte Clash Royale",
        description=(
            "Clique sur le bouton pour lier ton compte Discord à ton joueur :\n"
            "**1.** saisis ton **tag** Clash Royale (le bot vérifie qu'il est correct) ;\n"
            "**2.** prouve que le compte est le tien (le bot t'explique comment : jeton API à coller, ou carte favorite à choisir).\n\n"
            "Ensuite, le bot te renomme avec ton pseudo en jeu et te donne le rôle de ton rang dans le clan."
        ),
        color=discord.Color.green(),
    )
