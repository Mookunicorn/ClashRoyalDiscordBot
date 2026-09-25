"""Mise à jour automatique du bot depuis GitHub, via `git pull` sur le dépôt local.

Ne fonctionne que si le bot tourne depuis un clone Git (dossier contenant un `.git`).
Désactivée par défaut — à activer avec `/configurer` (étape 3, « Mise à jour automatique »).

⚠️ Implication de sécurité : activer cette option donne à quiconque peut pousser sur la
branche suivie du dépôt la capacité d'exécuter du code sur la machine qui héberge le bot
(au prochain cycle de vérification). Ne l'active que sur un dépôt que tu maîtrises seul,
protège la branche suivie si tu ajoutes des collaborateurs, et ne l'active pas si le dépôt
accepte des contributions externes fusionnées automatiquement.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

log = logging.getLogger("updater")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def is_git_repo() -> bool:
    return os.path.isdir(os.path.join(REPO_ROOT, ".git"))


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=REPO_ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


async def behind_count(bot) -> int | None:
    """Nombre de commits en amont sur la branche suivie ; None si indisponible (pas un dépôt
    Git, `git` absent, pas de branche amont configurée, ou erreur réseau)."""
    if not is_git_repo():
        return None
    code, out = await _run("git", "fetch", "--quiet")
    if code != 0:
        log.warning("git fetch a échoué : %s", out.strip()[-300:])
        return None
    code, out = await _run("git", "rev-list", "--count", "HEAD..@{u}")
    if code != 0:
        log.warning("Impossible de comparer avec la branche amont (non configurée ?) : %s", out.strip()[-300:])
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


async def apply_update(bot) -> bool:
    """Récupère les derniers commits, réinstalle les dépendances si besoin, puis relance le
    bot dans le même processus (`os.execv`) — ne revient jamais si tout se passe bien."""
    code, out = await _run("git", "pull", "--ff-only")
    if code != 0:
        log.error("git pull a échoué, mise à jour annulée :\n%s", out)
        return False

    pip = os.path.join(sys.prefix, "bin", "pip")
    if os.path.exists(pip):
        code, out = await _run(pip, "install", "-q", "-r", "requirements.txt")
        if code != 0:
            log.error("Échec de l'installation des dépendances, mise à jour annulée :\n%s", out)
            return False

    log.info("Mise à jour appliquée, redémarrage du bot…")
    try:
        await bot.close()
    except Exception:
        log.exception("Fermeture propre échouée, redémarrage quand même")
    await asyncio.sleep(1.5)  # laisse le temps aux derniers messages Discord de partir
    os.execv(sys.executable, [sys.executable] + sys.argv)
    return True  # jamais atteint si execv réussit
