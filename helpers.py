"""Fonctions partagées par les commandes."""
from __future__ import annotations

import discord
from discord import app_commands

from config import normalize_tag
from embeds import esc


async def member_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.lower().lstrip("#")
    out = []
    for m in sorted(interaction.client.members.values(), key=lambda m: m["name"].lower()):
        if needle in m["name"].lower() or needle in m["tag"].lower():
            out.append(app_commands.Choice(name=f"{m['name']} ({m['tag']})"[:100], value=m["tag"]))
        if len(out) == 25:
            break
    return out


async def resolve_tag(bot, value: str) -> str:
    """Nom en jeu ou tag -> tag normalisé."""
    v = value.strip()
    as_tag = normalize_tag(v)
    for m in bot.members.values():
        if m["name"].lower() == v.lower() or m["tag"] == as_tag:
            return m["tag"]
    return as_tag


# ---------------------------------------------------------------------- erreurs et diagnostics partagés
async def handle_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Message d'erreur lisible pour toutes les commandes (API, configuration, inattendu)."""
    import logging

    from cr_api import ClashAPIError, NotConfigured, NotFound

    original = getattr(error, "original", error)
    if isinstance(original, NotConfigured):
        message = "⚙️ Le bot n'est pas encore configuré : un admin doit lancer `/configurer`."
    elif isinstance(original, NotFound):
        message = "Introuvable (tag, identifiant ou nom incorrect)."
    elif isinstance(original, ClashAPIError):
        hint = " Vérifie la clé API et son IP autorisée (`/configurer` puis « Tester »)." if original.status in (401, 403) else ""
        message = f"Erreur de l'API Clash Royale ({original.status}).{hint}"
    elif isinstance(error, app_commands.CheckFailure):
        return  # déjà géré par le check (message envoyé)
    else:
        logging.getLogger("commands").exception("Erreur de commande", exc_info=original)
        message = "Une erreur inattendue est survenue."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def require_config(interaction: discord.Interaction, clan: bool) -> bool:
    """À utiliser dans Cog.interaction_check : clé API (et clan si `clan`) requis."""
    cfg = interaction.client.cfg
    if cfg.cr_api_key and (cfg.clan_tag or not clan):
        return True
    await interaction.response.send_message(
        "⚙️ Le bot n'est pas encore configuré : un admin doit d'abord lancer `/configurer`.", ephemeral=True
    )
    return False


async def check_token_endpoint(bot) -> tuple[str, str]:
    """Teste l'endpoint de vérification par jeton avec un faux jeton -> (niveau, message)."""
    tag = next(iter(bot.members), None)
    if tag is None:
        return "warn", "Liste des membres pas encore chargée : réessaie dans un instant."
    try:
        result = await bot.api.verify_token(tag, "jeton-bidon-de-test")
    except Exception as exc:  # noqa: BLE001 - diagnostic
        return "error", f"Erreur réseau/API : {exc}"
    return {
        "invalid": ("ok", "L'endpoint de vérification répond et **refuse** un faux jeton : liaison par jeton utilisable."),
        "ok": ("error", "L'API a **accepté** un faux jeton : vérification non fiable. Relance `/configurer` et choisis le mode « carte »."),
        "unavailable": ("warn", "L'endpoint est **refusé** pour ta clé (401/403) : liaison par jeton indisponible. Relance `/configurer` et choisis le mode « carte »."),
        "notfound": ("warn", "Tag de test introuvable, réessaie."),
    }[result]


# ---------------------------------------------------------------------- résolution salon/rôle par texte (assistant /configurer)
def resolve_channel(guild, raw: str):
    """Mention <#id>, identifiant, ou nom de salon -> discord.abc.GuildChannel | None."""
    raw = raw.strip()
    if not raw:
        return None
    digits = raw.strip("<#>")
    if digits.isdigit():
        return guild.get_channel(int(digits))
    lowered = raw.lstrip("#").lower()
    return discord.utils.find(lambda c: c.name.lower() == lowered, guild.text_channels)


def resolve_role(guild, raw: str):
    """Mention <@&id>, identifiant, ou nom de rôle -> discord.Role | None."""
    raw = raw.strip()
    if not raw:
        return None
    digits = raw.strip("<@&>")
    if digits.isdigit():
        return guild.get_role(int(digits))
    lowered = raw.lower()
    return discord.utils.find(lambda r: r.name.lower() == lowered, guild.roles)


def channel_issues(channel) -> list[str]:
    perms = channel.permissions_for(channel.guild.me)
    needed = (("voir le salon", perms.view_channel), ("envoyer des messages", perms.send_messages),
              ("intégrer des liens", perms.embed_links))
    return [name for name, ok in needed if not ok]


def key_hint(status: int) -> str:
    return (
        " Vérifie que la clé est complète et que son IP autorisée est celle de la machine du bot "
        "(ou utilise un proxy comme https://proxy.royaleapi.dev/v1, avec l'IP `45.79.218.79` sur la clé)."
        if status in (401, 403) else ""
    )


async def build_diagnostic_embed(bot, guild) -> discord.Embed:
    """Diagnostic complet : API, clan, salons, permissions, rôles, liaison. Utilisé par /configurer."""
    from cr_api import ClashAPIError
    from settings import BY_KEY

    cfg = bot.cfg
    lines: list[str] = []
    icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}

    def add(level: str, text: str) -> None:
        lines.append(f"{icon[level]} {text}")

    if not cfg.cr_api_key:
        add("error", "Clé API non renseignée")
    else:
        try:
            await bot.api.get("/locations", {"limit": 1})
            add("ok", "Clé API valide")
        except ClashAPIError as exc:
            add("error", f"Clé API refusée ({exc.status}).{key_hint(exc.status)}")
    if cfg.clan_tag and bot.api.configured:
        try:
            info = await bot.api.clan(cfg.clan_tag)
            add("ok", f"Clan **{esc(info.get('name', '?'))}** ({info.get('members', '?')}/50)")
        except ClashAPIError as exc:
            add("error", f"Clan `{cfg.clan_tag}` inaccessible ({exc.status})")
    elif not cfg.clan_tag:
        add("error", "Clan non renseigné")

    for key in ("salon_arrivees", "salon_departs", "salon_logs", "salon_guerre", "salon_accueil"):
        spec = BY_KEY[key]
        cid = getattr(cfg, spec.attr)
        if not cid:
            add("error" if spec.required else "warn", f"{spec.label} : non défini" + ("" if spec.required else " (repli automatique)"))
            continue
        channel = bot.get_channel(cid)
        if channel is None:
            add("error", f"{spec.label} : salon introuvable")
            continue
        issues = channel_issues(channel)
        add("warn" if issues else "ok", f"{spec.label} : {channel.mention}" + (f" — il me manque : {', '.join(issues)}" if issues else ""))

    if guild is not None:
        perms = guild.me.guild_permissions
        add("ok" if perms.manage_roles else "warn", "Permission « Gérer les rôles »" + ("" if perms.manage_roles else " manquante"))
        add("ok" if perms.manage_nicknames else "warn", "Permission « Gérer les pseudos »" + ("" if perms.manage_nicknames else " manquante"))
        mapping = await bot.db.rank_roles()
        if not mapping:
            add("warn", "Aucun rôle de rang configuré (`/rang_config`)")
        for rank, rid in mapping.items():
            role = guild.get_role(rid)
            if role is None:
                add("error", f"Rôle de « {rank} » supprimé")
            elif not role.is_assignable():
                add("warn", f"@{role.name} : place le rôle du bot au-dessus")
    else:
        add("warn", "Serveur introuvable")
    add("ok" if cfg.members_intent else "warn",
        "Intent « Membres » actif (accueil automatique)" if cfg.members_intent else "MEMBERS_INTENT=false : pas d'accueil automatique")
    add("ok" if cfg.daily_report_enabled else "warn",
        f"Rapport quotidien activé (envoyé à {cfg.daily_report_time.strftime('%H:%M')})" if cfg.daily_report_enabled
        else "Rapport quotidien désactivé")

    add("ok", {"carte": "Liaison par **carte favorite** (aucun accès spécial requis)",
              "token": "Liaison par jeton API", "clan": "⚠️ Liaison sans preuve de propriété"}[cfg.link_verification])
    if cfg.cr_api_key and bot.members and cfg.link_verification == "token":
        level, text = await check_token_endpoint(bot)
        add(level, text)
    linked = len(await bot.db.linked_players())
    add("ok", f"Comptes liés : {linked}")

    return discord.Embed(
        title="🩺 Diagnostic", description="\n".join(lines),
        color=discord.Color.red() if any(l.startswith("❌") for l in lines)
        else discord.Color.orange() if any(l.startswith("⚠️") for l in lines) else discord.Color.green())
