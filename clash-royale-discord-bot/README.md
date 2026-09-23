# Bot Discord — Clash Royale

Bot Discord complet pour un clan Clash Royale : suivi quasi instantané des arrivées et
départs, pseudo Discord synchronisé sur le pseudo en jeu, rôles Discord selon le rang dans
le clan, liaison de compte protégée contre l'usurpation, et l'essentiel de l'API officielle
Clash Royale accessible par commandes. **Tout se configure depuis Discord**, sans toucher
à un fichier.

## Démarrage express

1. Crée l'application Discord et invite le bot (voir « Installation » plus bas).
2. Renseigne uniquement `DISCORD_TOKEN` dans `.env` et lance le bot.
3. Sur ton serveur, lance **`/configurer`** — c'est la seule commande de réglage : le bot
   t'écrit en message privé, avec 4 étapes courtes (clé API/clan, salons, rôles/
   surveillance, guerre/liaison) accessibles **dans n'importe quel ordre** via un menu
   déroulant — pas besoin de repasser par les étapes précédentes pour n'en changer qu'une
   seule. Chaque champ préaffiche sa valeur actuelle ; le laisser vide le conserve tel
   quel, donc relancer `/configurer` sert aussi bien à tout régler la première fois qu'à
   revoir un seul réglage plus tard.

Rien de ce qui est saisi dans cet assistant — clé API comprise — n'apparaît jamais dans un
salon du serveur : tout se passe en message privé. Rien d'autre n'est obligatoire : les
salons de départs/logs/guerre retombent sur le salon des arrivées tant qu'ils ne sont pas
définis, la surveillance démarre à 10 secondes, la liaison de compte utilise un défi sans
permission spéciale (voir plus bas).

## Fonctionnalités

**Suivi du clan (quasi instantané)**
- 🎉 **Arrivée** : message immédiat (nom en jeu, nombre de passages dans le clan) puis
  complété quelques secondes plus tard avec les 5 derniers clans et la moyenne des 5
  dernières guerres (gloire + decks). La surveillance tourne par défaut toutes les 10
  secondes (réglable, jusqu'à 5 s) : c'est la seule façon de détecter un changement, l'API
  Clash Royale n'a pas de notifications.
- 👋 **Départ** immédiat, avec la durée passée dans le clan
- ⬆️/⬇️ Promotions et rétrogradations
- ⚙️ **Réglages du clan** : passage ouvert / sur invitation / fermé, trophées requis, nom,
  description, pays — annoncé dans le salon des logs dès qu'un changement est détecté
- 📅 **Rapport quotidien** dans le salon de guerre (désactivé par défaut) : à une heure réglable, un résumé du jour (entraînement / jour de guerre, classement du clan, decks encore à jouer). Se règle dans `/configurer` (étape 4).
- 🔄 Ralentissement automatique et reprise en cas d'erreur ou de limite de débit de l'API

**Discord ↔ Clash Royale**
- 🔗 **Liaison protégée contre l'usurpation**, en message privé au nouvel arrivant (repli
  sur un salon si ses MP sont fermés) : tag vérifié (format, existence, présence dans le
  clan), puis preuve de propriété au choix (voir « Sécurité de la liaison »). C'est un
  parcours séparé de `/configurer`, propre à chaque joueur qui rejoint le Discord.
- Le bot **renomme automatiquement** le membre avec son pseudo Clash Royale (pas de prénom
  à saisir : le pseudo Discord suit le pseudo en jeu) et lui donne le **rôle correspondant
  à son rang** (Membre / Aîné / Chef adjoint / Chef, + un rôle commun optionnel)
- Mise à jour automatique à la moindre promotion, départ (rôles retirés) ou changement de
  pseudo en jeu

**Commandes du clan**
| Commande | Rôle |
|---|---|
| `/clan [tag]` | Infos générales (ton clan, ou un autre avec son tag) |
| `/membres [tri]` | Tableau des membres (rang, trophées, dons) |
| `/joueur` | Fiche complète : profil, statistiques, Path of Legends, deck actuel, passages, 5 derniers clans, moyenne des guerres |
| `/guerre` | État de la course en cours (tous les clans) |
| `/guerre_joueurs` | Classement des joueurs de ton clan dans la guerre en cours |
| `/decks` | Qui n'a pas fini ses 4 decks aujourd'hui |
| `/historique_guerre` | Résultats des dernières guerres du clan |
| `/ancienne_guerre [tag]` | Ancien système de guerre de clan (avant les River Race), si le clan l'utilise encore |
| `/moyennes` | Moyenne de gloire par joueur sur N guerres |
| `/inactifs` | Membres inactifs depuis N jours |
| `/dons` | Top / flop des dons |
| `/coffres` | Prochains coffres d'un joueur |

**Cartes, tournois, classements, pays** (fonctionnent sur tout le jeu, pas que ton clan)
| Commande | Rôle |
|---|---|
| `/cartes [rareté]`, `/carte` | Liste ou détail d'une carte |
| `/recherche_clans` | Rechercher des clans par nom |
| `/tournois`, `/tournoi` | Rechercher / afficher un tournoi |
| `/tournois_mondiaux` | Tournois mondiaux en cours |
| `/pays` | Rechercher un pays (pour les classements) |
| `/classement`, `/classement_legende` | Classements clans/joueurs/guerres, Path of Legends |
| `/saisons`, `/saison` | Saisons de ligue et leur classement |
| `/evenements` | Événements en cours |
| `/classements_speciaux`, `/classement_special` | Classements spéciaux (leaderboards) |

**Configuration et administration** (staff / admin uniquement)
| Commande | Rôle |
|---|---|
| `/configurer` | **La seule commande de réglage** : assistant en message privé avec 4 étapes (clé API, clan ; salons ; rôle staff, surveillance, fuseau ; rappels, rapport quotidien, liaison, URL API), accessibles dans n'importe quel ordre via un menu déroulant à chaque étape. Relançable à tout moment ; un bouton « Tester » est toujours disponible. |
| `/rang_config`, `/rang_retirer`, `/rang_liste` | Associer un rang du clan à un rôle Discord |
| `/renommage` | Activer/désactiver le renommage automatique |
| `/sync` | Forcer la synchro pseudos + rôles |
| `/lier`, `/delier` | Lier/délier manuellement un compte (sans preuve — dernier recours) |
| `/panneau` | Poster le bouton « Lier mon compte » dans le salon courant |
| `/liaison_test` | Tester si la vérification par jeton API fonctionne avec ta clé |
| `/verifier` | Vérifier un joueur : tag, présence dans le clan, liaison, pseudo, rôles |
| `/verifier_tous` | Audit complet du clan (non liés, non vérifiés, désynchronisés…) |

## Installation

### 1. Discord
1. https://discord.com/developers/applications → **New Application** → onglet **Bot** →
   copie le **token**.
2. *Privileged Gateway Intents* → active **Server Members Intent** (détecte les arrivées
   sur le Discord). Sans lui : `MEMBERS_INTENT=false` dans `.env` — tout marche sauf
   l'accueil automatique ; le bouton posé avec `/panneau` reste utilisable.
3. Invitation : scopes `bot` + `applications.commands`, permissions *Envoyer des
   messages*, *Intégrer des liens*, **Gérer les rôles** et **Gérer les pseudos**.
4. **Hiérarchie des rôles** : dans Paramètres du serveur → Rôles, place le rôle du bot
   **au-dessus** des rôles de rang qu'il doit attribuer. Le propriétaire du serveur ne
   peut jamais être renommé par un bot.
5. Le mode développeur Discord n'est pas nécessaire : `/configurer` accepte le nom, la
   mention ou l'identifiant d'un salon ou d'un rôle, au choix.

### 2. Clé API Clash Royale
https://developer.clashroyale.com → **My Account** → **Create New Key**, liée à l'adresse
IP publique de la machine qui fera tourner le bot. Si cette IP change souvent (box perso),
utilise le proxy RoyaleAPI : autorise l'IP `45.79.218.79` sur la clé et règle, dans
`/configurer` (étape 4), le champ « URL de l'API » sur `https://proxy.royaleapi.dev/v1`
(vérifie l'IP actuelle dans la doc RoyaleAPI, elle peut changer).

### 3. Lancer (Windows / PowerShell)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # renseigne DISCORD_TOKEN (le reste se règle sur Discord)
python bot.py
```
Linux/macOS : `python3 -m venv .venv && source .venv/bin/activate`.
Docker : `docker compose up -d --build`.

Puis sur Discord : `/configurer` (et rien d'autre pour démarrer).

Tests hors-ligne (API simulée) : `python -m unittest discover tests -v`

## Sécurité de la liaison Discord ↔ joueur

L'API Clash Royale ne permet pas de prouver directement qu'un tag appartient à la personne
qui le saisit. Le bot propose trois niveaux, réglables via `/configurer` (étape 4, champ
« Vérification de liaison ») :

- **`token` (par défaut, recommandé)** — le joueur colle son **jeton API** (Paramètres du
  jeu → tout en bas), vérifié directement par Supercell
  (`POST /players/{tag}/verifytoken`). C'est le plus simple pour le joueur : un copier-
  coller, sans rien changer dans sa partie. Cet endpoint s'est révélé **refusé (HTTP
  401/403)** pour certaines clés développeur lors des essais — utilise `/liaison_test`, ou
  le bouton « Tester » de `/configurer`, pour vérifier que ta clé y a accès ; si ce n'est
  pas le cas, passe au mode `carte` ci-dessous.
- **`carte`** — après avoir saisi son tag, le joueur doit changer sa **carte favorite** en
  jeu pour une carte précise que le bot lui indique (aléatoire, différente de sa carte
  actuelle), puis cliquer sur « Vérifier ». Plus de manipulations pour le joueur, mais ne
  nécessite aucun accès spécial à l'API — un filet de sécurité qui fonctionne avec
  n'importe quelle clé développeur standard si `token` est indisponible.
- **`clan`** — seule la présence du tag dans le clan est vérifiée, **sans preuve de
  propriété**. À réserver aux cas où les deux autres modes sont inutilisables.

Dans tous les cas : un compte Discord ne peut être lié qu'à un seul joueur, un joueur déjà
lié ne peut pas être repris par quelqu'un d'autre, et chaque liaison réussie est tracée
dans le salon des logs (`method` précise si elle a été vérifiée ou non). `/verifier` et
`/verifier_tous` permettent au staff d'auditer l'état des liaisons à tout moment, et
`/lier` reste disponible en dernier recours pour une liaison manuelle sans preuve.

La clé API elle-même est **chiffrée** dans la base SQLite (jamais en clair), avec une clé
de chiffrement dérivée de `DISCORD_TOKEN` (ou de `SECRET_KEY` si tu la définis) — si tu
changes le token du bot, la clé API stockée redevient illisible et il faut la ressaisir
avec `/configurer`.

## Ce que l'API permet — et ne permet pas

- **Détection en temps réel** : l'API n'a pas de webhooks. Le bot interroge le clan à
  intervalle court (10 s par défaut) ; c'est le meilleur délai possible avec cette API.
- **Nombre de passages dans le clan** : compté par le bot à partir de son installation
  (les membres déjà présents au premier lancement comptent pour 1 passage, marqué « depuis
  au moins… »). Si un nouveau apparaît dans les journaux de guerre du clan sans passage
  suivi, l'embed signale « ancien membre probable ».
- **5 derniers clans** : l'API n'a pas d'historique de clans. Le bot lit le **journal de
  combats** (25 derniers), qui indique le clan du joueur au moment de chaque combat, et
  mémorise les clans croisés. Un joueur inactif aura peu ou pas de clans listés.
- **Moyenne des 5 dernières guerres** : pour chaque clan détecté, le bot lit son journal de
  guerre (`riverracelog`) et y retrouve la gloire et les decks du joueur ; si le joueur a
  joué la même guerre dans deux clans, les scores sont additionnés.
- **Ancien système de guerre** (`/ancienne_guerre`, `currentwar`/`warlog`) : Supercell l'a
  remplacé par les Guerres de clans actuelles (River Race) pour la quasi-totalité des
  clans ; l'API renvoie une erreur pour un clan qui utilise le nouveau système — le bot
  l'affiche clairement plutôt que de planter.

## Réglages
Tout se règle avec `/configurer` (voir « Démarrage express »). `.env` ne sert qu'à
`DISCORD_TOKEN` et à quelques valeurs initiales optionnelles (voir `.env.example`) :
une valeur définie ensuite via `/configurer` prime toujours sur `.env`.

## Structure
```
bot.py             démarrage, chargement des extensions
config.py          structure de configuration (valeurs par défaut)
settings.py        réglages modifiables depuis Discord (validation, chiffrement, /config)
cr_api.py          client API (tous les endpoints documentés, retries, cache)
database.py        SQLite (joueurs, passages, clans croisés, rôles de rang, réglages)
discord_sync.py    pseudo en jeu + rôles Discord selon le rang (plan / apply)
history.py         reconstruction des 5 derniers clans + moyenne de guerre
embeds.py          mise en forme des messages
ui.py              boutons persistants, modales, défi carte favorite
helpers.py         résolution nom/tag, autocomplétion, erreurs communes
war_utils.py        état de la guerre en cours (decks restants)
cogs/tracker.py     arrivées / départs / promotions / réglages du clan
cogs/war.py         rappels de decks + récap de guerre
cogs/commands.py    commandes du clan suivi
cogs/explorer.py    cartes, tournois, classements, pays, saisons, événements
cogs/discord_roles.py  rangs → rôles, renommage, accueil, liaison
cogs/verification.py   /verifier, /verifier_tous
cogs/setup.py           /configurer (assistant privé, tout le réglage en une commande)
tests/              tests hors-ligne (API et Discord simulés)
```
