"""Tests hors-ligne (API simulée) : python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

import cogs.setup as setup_mod  # noqa: E402
import ui  # noqa: E402
from config import Config  # noqa: E402
from cogs.tracker import Tracker  # noqa: E402
from cogs.war import War  # noqa: E402
from embeds import build_daily_report_embed  # noqa: E402
from updater import apply_update, behind_count, is_git_repo  # noqa: E402
from settings import BY_KEY, Settings, SettingError, parse as parse_setting  # noqa: E402
from cr_api import ClashAPIError, ClashRoyaleAPI, NotFound  # noqa: E402
from discord_sync import ALL_KEY, RoleSync, nick_for  # noqa: E402
from database import Database  # noqa: E402
from embeds import build_leave_embed, build_player_embed, build_war_recap  # noqa: E402
from history import build_history  # noqa: E402

OWN = "#OWN"
PLAYER = "#P1"


def race(season, section, date, clan_tag, clan_name, fame, decks, player=PLAYER):
    return {
        "seasonId": season, "sectionIndex": section, "createdDate": date,
        "standings": [
            {"rank": 1, "trophyChange": 20, "clan": {
                "tag": clan_tag, "name": clan_name, "fame": fame * 10,
                "participants": [{"tag": player, "name": "Bob", "fame": fame, "decksUsed": decks}],
            }},
            # autre clan de la même course : ne doit jamais être pris en compte
            {"rank": 2, "trophyChange": 0, "clan": {
                "tag": "#OTHER", "name": "Other", "fame": 1,
                "participants": [{"tag": player, "name": "Bob", "fame": 99999, "decksUsed": 16}],
            }},
        ],
    }


class FakeAPI:
    def __init__(self):
        self.members = []
        self.clan_extra = {}
        self.battles = [
            {"battleTime": "20260910T100000.000Z", "team": [{"tag": PLAYER, "clan": {"tag": "#A", "name": "Clan A"}}]},
            {"battleTime": "20260911T100000.000Z", "team": [{"tag": PLAYER, "clan": {"tag": "#A", "name": "Clan A"}}]},
            {"battleTime": "20260801T100000.000Z", "team": [{"tag": PLAYER, "clan": {"tag": "#B", "name": "Clan B"}}]},
            {"battleTime": "20260701T100000.000Z", "team": [{"tag": PLAYER}]},  # sans clan
        ]
        self.logs = {
            "#A": [race(120, 2, "20260908T094412.000Z", "#A", "Clan A", 2400, 16),
                   race(120, 1, "20260901T094412.000Z", "#A", "Clan A", 2000, 14)],
            "#B": [race(119, 3, "20260825T094412.000Z", "#B", "Clan B", 1000, 8),
                   race(119, 2, "20260818T094412.000Z", "#B", "Clan B", 3000, 16),
                   race(119, 1, "20260811T094412.000Z", "#B", "Clan B", 2600, 16),
                   race(119, 0, "20260804T094412.000Z", "#B", "Clan B", 500, 4)],
            OWN: [],
        }

    async def clan(self, tag, ttl=0):
        return {"memberList": list(self.members), **self.clan_extra}

    async def player(self, tag, ttl=0):
        return {"tag": tag, "name": "Bob", "trophies": 7000, "bestTrophies": 7800, "expLevel": 14,
                "arena": {"name": "Arène Légendaire"}}

    async def battle_log(self, tag, ttl=120):
        return self.battles

    async def river_race_log(self, tag, limit=30, ttl=300):
        return self.logs.get(tag, [])


class FakeSync:
    def __init__(self):
        self.calls = []

    async def apply_safe(self, tag):
        self.calls.append(tag)

    async def sync_all(self):
        self.calls.append("ALL")
        return {}


def member(tag, name, role="member"):
    return {"tag": tag, "name": name, "role": role, "trophies": 6000}


class Core(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "t.db"))
        await self.db.connect()
        self.api = FakeAPI()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_history(self):
        h = await build_history(self.api, self.db, PLAYER, OWN)
        self.assertEqual([c["clan_tag"] for c in h.previous_clans], ["#A", "#B"])
        # 5 guerres les plus récentes : A(2400,2000) puis B(1000,3000,2600) — la 4e guerre de B est exclue
        self.assertEqual([w.fame for w in h.wars], [2400, 2000, 1000, 3000, 2600])
        self.assertAlmostEqual(h.avg_fame, (2400 + 2000 + 1000 + 3000 + 2600) / 5)
        # le clan « Other » (99999) n'est jamais compté
        self.assertTrue(all(w.fame < 99999 for w in h.wars))

    async def test_stints_and_count(self):
        await self.db.open_stint(PLAYER)
        await self.db.close_stint(PLAYER)
        await self.db.open_stint(PLAYER)
        self.assertEqual(len(await self.db.stints(PLAYER)), 2)
        self.assertEqual(await self.db.open_tags(), {PLAYER})

    async def test_tracker_join_leave_role(self):
        sent = []
        bot = SimpleNamespace(
            api=self.api, db=self.db, members={}, clan_info={}, sync=FakeSync(),
            cfg=SimpleNamespace(ready=True, clan_tag=OWN, join_channel_id=1, log_channel_id=2,
                                war_channel_id=3, leave_channel_id=2),
        )
        tracker = Tracker(bot)

        async def fake_send(channel_id, **kw):
            sent.append((channel_id, kw))
            return None   # pas de message.edit ultérieur (comportement testé séparément)
        tracker.send = fake_send

        self.api.members = [member("#X1", "Alice"), member("#X2", "Zed", "elder")]
        await tracker.run_once()
        self.assertEqual(sent, [])  # initialisation silencieuse

        bot.sync.calls.clear()
        self.api.members.append(member(PLAYER, "Bob"))
        await tracker.run_once()
        self.assertIn(PLAYER, bot.sync.calls)   # arrivée dans le clan -> synchro Discord
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 1)
        embed = sent[0][1]["embed"]
        names = [f.name for f in embed.fields]
        self.assertEqual(names[0], "🎮 Nom en jeu")
        self.assertIn("🔁 Passages dans le clan", names)
        self.assertIn("1ʳᵉ fois", [f.value for f in embed.fields if f.name.startswith("🔁")][0])

        await tracker.run_once()  # pas de doublon
        self.assertEqual(len(sent), 1)

        self.api.members = [m for m in self.api.members if m["tag"] != PLAYER]
        self.api.members[1]["role"] = "coLeader"
        bot.sync.calls.clear()
        await tracker.run_once()
        self.assertEqual([c for c, _ in sent], [1, 2, 2])  # départ + promotion → salon logs
        self.assertCountEqual(bot.sync.calls, [PLAYER, "#X2"])  # départ + promotion -> synchro

        self.api.members.append(member(PLAYER, "Bob"))  # retour
        await tracker.run_once()
        emb = sent[-1][1]["embed"]
        self.assertIn("2ᵉ fois", [f.value for f in emb.fields if f.name.startswith("🔁")][0])

    async def test_tracker_ignores_suspicious_drop(self):
        sent = []
        bot = SimpleNamespace(api=self.api, db=self.db, members={}, clan_info={}, sync=FakeSync(),
                              cfg=SimpleNamespace(ready=True, clan_tag=OWN, join_channel_id=1, log_channel_id=2,
                                                  war_channel_id=3, leave_channel_id=2))
        tracker = Tracker(bot)

        async def fake_send(channel_id, **kw):
            sent.append(kw)
        tracker.send = fake_send
        self.api.members = [member(f"#M{i}", f"M{i}") for i in range(10)]
        await tracker.run_once()
        self.api.members = self.api.members[:2]
        await tracker.run_once()
        self.assertEqual(sent, [])

    def test_war_recap(self):
        r = race(120, 2, "20260908T094412.000Z", OWN, "Nous", 2400, 16)
        self.assertIsNotNone(build_war_recap(r, OWN))
        self.assertIsNone(build_war_recap(r, "#NOPE"))


class FakeRole:
    def __init__(self, rid, name, assignable=True):
        self.id, self.name, self._ok = rid, name, assignable

    def is_assignable(self):
        return self._ok


class FakeMember:
    def __init__(self, mid, roles=(), nick=None, name="discorduser", forbid_edit=False):
        self.id, self.roles, self.nick, self.name, self.forbid_edit = mid, list(roles), nick, name, forbid_edit

    @property
    def display_name(self):
        return self.nick or self.name

    async def edit(self, nick=None, reason=None):
        if self.forbid_edit:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        self.nick = nick

    async def add_roles(self, *roles, reason=None):
        self.roles += [r for r in roles if r not in self.roles]

    async def remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]


class FakeGuild:
    def __init__(self, roles, members, owner_id=0):
        self._roles = {r.id: r for r in roles}
        self._members = {m.id: m for m in members}
        self.owner_id = owner_id

    def get_role(self, rid):
        return self._roles.get(rid)

    def get_member(self, mid):
        return self._members.get(mid)

    async def fetch_member(self, mid):
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")


class RoleSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "r.db"))
        await self.db.connect()
        self.r_all, self.r_member, self.r_elder = FakeRole(9, "Clan"), FakeRole(10, "Membre"), FakeRole(11, "Aîné")
        self.everyone_member = FakeMember(100)
        self.guild = FakeGuild([self.r_all, self.r_member, self.r_elder], [self.everyone_member])
        self.bot = SimpleNamespace(
            cfg=SimpleNamespace(guild_id=1), db=self.db, guilds=[self.guild],
            get_guild=lambda gid: self.guild, members={"#X1": member("#X1", "Alice")},
        )
        self.sync = RoleSync(self.bot)
        await self.db.set_rank_role("member", 10)
        await self.db.set_rank_role("elder", 11)
        await self.db.set_rank_role(ALL_KEY, 9)
        await self.db.set_discord_id("#X1", 100, "Alice")

    async def asyncTearDown(self):
        await self.db.close()

    async def test_rename_and_roles_lifecycle(self):
        m = self.everyone_member
        rep = await self.sync.apply("#X1")
        self.assertEqual(m.nick, "Alice")
        self.assertCountEqual([r.id for r in m.roles], [9, 10])
        self.assertTrue(rep.changed)

        # promotion : Membre -> Aîné
        self.bot.members["#X1"]["role"] = "elder"
        await self.sync.apply("#X1")
        self.assertCountEqual([r.id for r in m.roles], [9, 11])

        # changement de pseudo en jeu
        self.bot.members["#X1"]["name"] = "AliceV2"
        await self.sync.apply("#X1")
        self.assertEqual(m.nick, "AliceV2")

        # rôles non gérés par le bot : jamais touchés
        other = FakeRole(77, "Modo")
        m.roles.append(other)
        await self.sync.apply("#X1")
        self.assertIn(other, m.roles)

        # départ du clan : rôles retirés, pseudo conservé
        self.bot.members = {"#X9": member("#X9", "Autre")}
        await self.sync.apply("#X1")
        self.assertEqual([r.id for r in m.roles], [77])
        self.assertEqual(m.nick, "AliceV2")

        # idempotent
        rep = await self.sync.apply("#X1")
        self.assertFalse(rep.changed)

    async def test_empty_member_list_never_strips(self):
        await self.sync.apply("#X1")
        self.bot.members = {}
        rep = await self.sync.apply("#X1")
        self.assertEqual(len(self.everyone_member.roles), 2)
        self.assertTrue(rep.warnings)

    async def test_warnings_when_not_permitted(self):
        self.r_member._ok = False
        self.everyone_member.forbid_edit = True
        rep = await self.sync.apply("#X1")
        self.assertEqual(len(rep.warnings), 2)
        self.assertEqual([r.id for r in self.everyone_member.roles], [9])

    async def test_rename_disabled_and_enabled(self):
        await self.db.set_meta("rename_enabled", "0")
        await self.sync.apply("#X1")
        self.assertIsNone(self.everyone_member.nick)
        await self.db.set_meta("rename_enabled", "1")
        await self.sync.apply("#X1")
        self.assertEqual(self.everyone_member.nick, "Alice")   # pseudo Discord = nom en jeu

    async def test_member_absent_from_discord(self):
        await self.db.set_discord_id("#X1", 555, "Alice")   # pas sur le serveur
        rep = await self.sync.apply("#X1")
        self.assertFalse(rep.found)

    def test_nick_for(self):
        self.assertEqual(nick_for("Bob"), "Bob")
        self.assertEqual(nick_for("  Bob   Le Fou  "), "Bob Le Fou")
        self.assertEqual(len(nick_for("x" * 50)), 32)


class FakeHTTPResp:
    def __init__(self, status, body=""):
        self.status, self._body = status, body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, json=None, headers=None):
        self.calls.append((str(url), json))
        return self.resp


class VerifyTokenTests(unittest.IsolatedAsyncioTestCase):
    async def result(self, status, body=""):
        api = ClashRoyaleAPI("k", "https://x/v1")
        api._session = FakeSession(FakeHTTPResp(status, body))
        res = await api.verify_token("#2PPY", "secret")
        return res, api._session.calls

    async def test_mapping(self):
        res, calls = await self.result(200, '{"tag": "#2PPY", "token": "secret", "status": "ok"}')
        self.assertEqual(res, "ok")
        self.assertEqual(calls[0][0], "https://x/v1/players/%232PPY/verifytoken")
        self.assertEqual(calls[0][1], {"token": "secret"})
        self.assertEqual((await self.result(200, '{"status": "invalid"}'))[0], "invalid")
        self.assertEqual((await self.result(200, '{"tag": "#2PPY", "name": "Bob"}'))[0], "ok")   # format profil
        self.assertEqual((await self.result(200, '{"tag": "#OTHER"}'))[0], "invalid")
        self.assertEqual((await self.result(200, "n'importe quoi"))[0], "invalid")
        self.assertEqual((await self.result(400, '{"reason": "badRequest"}'))[0], "invalid")
        self.assertEqual((await self.result(404))[0], "notfound")
        self.assertEqual((await self.result(403))[0], "unavailable")
        with self.assertRaises(ClashAPIError):
            await self.result(500)


class FakeResponse:
    def __init__(self):
        self.sent, self.modal = [], None

    async def defer(self, ephemeral=False):
        pass

    async def send_message(self, *a, **k):
        self.sent.append((a, k))

    async def send_modal(self, modal):
        self.modal = modal


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append((content, kw))


class FakeInteraction:
    def __init__(self, client, user_id):
        self.client = client
        self.user = SimpleNamespace(id=user_id, mention=f"<@{user_id}>")
        self.response, self.followup = FakeResponse(), FakeFollowup()

    @property
    def last(self):
        return self.followup.sent[-1][0]


class LinkFlowTests(unittest.IsolatedAsyncioTestCase):
    TAG = "#2PPY"

    async def asyncSetUp(self):
        ui._FAILS.clear()
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "l.db"))
        await self.db.connect()
        self.log_sent, self.sync_calls, self.verify_calls = [], [], []
        outer = self

        class Chan:
            async def send(self, content=None, **kw):
                outer.log_sent.append(content)

        class API:
            result = "ok"

            async def player(self, tag, ttl=0):
                if tag == "#2GGG":
                    raise NotFound(404, "")
                return {"tag": tag, "name": "Visiteur"}

            async def verify_token(self, tag, token):
                outer.verify_calls.append((tag, token))
                return self.result

        class Sync:
            async def apply_safe(self, tag):
                outer.sync_calls.append(tag)
                return None

        self.api = API()
        self.bot = SimpleNamespace(
            db=self.db, api=self.api, sync=Sync(),
            members={self.TAG: {"tag": self.TAG, "name": "Alice", "role": "member"}},
            cfg=SimpleNamespace(link_verification="token", log_channel_id=2),
            get_channel=lambda cid: Chan(),
        )

    async def asyncTearDown(self):
        await self.db.close()

    async def step1(self, tag, user=1):
        modal = ui.LinkModal()
        modal.tag._value = tag
        it = FakeInteraction(self.bot, user)
        await modal.on_submit(it)
        return it

    async def step2(self, token, tag=None, user=1):
        modal = ui.TokenModal(tag or self.TAG)
        modal.jeton._value = token
        it = FakeInteraction(self.bot, user)
        await modal.on_submit(it)
        return it

    async def test_step1_incorrect_tags(self):
        self.assertIn("format", (await self.step1("#ABC")).last)
        self.assertIn("aucun joueur", (await self.step1("#2GGG")).last)
        self.assertIn("pas dans le clan", (await self.step1("#22PP")).last)   # existe mais hors clan

    async def test_step1_ok_then_token_view(self):
        it = await self.step1("2ppy")
        content, kw = it.followup.sent[-1]
        self.assertIn("Tag correct", content)
        self.assertIsInstance(kw["view"], ui.TokenView)
        self.assertIsNone(await self.db.find_by_discord(1))     # pas encore lié

    async def test_token_ok_links_and_syncs(self):
        it = await self.step2("le-bon-jeton")
        self.assertIn("✅", it.last)
        self.assertEqual((await self.db.find_by_discord(1))["tag"], self.TAG)
        self.assertEqual(self.sync_calls, [self.TAG])
        self.assertIn("jeton API", self.log_sent[0])
        self.assertEqual(self.verify_calls, [(self.TAG, "le-bon-jeton")])

    async def test_token_invalid_then_rate_limited(self):
        self.api.result = "invalid"
        for _ in range(ui.MAX_FAILS):
            self.assertIn("incorrect", (await self.step2("faux")).last)
        self.assertIsNone(await self.db.find_by_discord(1))
        n = len(self.verify_calls)
        self.assertIn("Trop d'essais", (await self.step2("faux")).last)
        self.assertEqual(len(self.verify_calls), n)             # plus aucun appel à l'API

    async def test_token_unavailable(self):
        self.api.result = "unavailable"
        it = await self.step2("x" * 8)
        self.assertIn("pas disponible", it.last)
        self.assertIsNone(await self.db.find_by_discord(1))

    async def test_cannot_take_someone_elses_link(self):
        await self.db.set_discord_id(self.TAG, 999, "Alice")
        it = await self.step2("le-bon-jeton", user=1)
        self.assertIn("déjà lié", it.last)
        self.assertEqual(self.verify_calls, [])                 # refusé avant même de vérifier
        self.assertEqual((await self.db.get_player(self.TAG))["discord_id"], 999)

    async def test_clan_only_mode(self):
        self.bot.cfg.link_verification = "clan"
        it = await self.step1(self.TAG)
        self.assertIn("✅ Compte lié", it.last)
        self.assertIn("sans vérification", self.log_sent[0])
        self.assertEqual(self.verify_calls, [])


class FakeWizardResponse:
    def __init__(self):
        self.modal = None

    async def defer(self, ephemeral=False):
        pass

    async def send_modal(self, modal):
        self.modal = modal


class FakeWizardFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append((content, kw))


class FakeWizardInteraction:
    def __init__(self, bot, user_id):
        self.client = bot
        self.user = SimpleNamespace(id=user_id)
        self.response, self.followup = FakeWizardResponse(), FakeWizardFollowup()


class FakeSettingsAPI:
    def __init__(self):
        self.configured = None

    def configure(self, key, url):
        self.configured = (key, url)


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "s.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    def make_bot(self, token="tok"):
        bot = SimpleNamespace(db=self.db, api=FakeSettingsAPI(), cfg=Config(discord_token=token), dispatch=lambda *a: None)
        return bot

    async def test_encrypted_key_persists_and_reloads(self):
        bot = self.make_bot()
        settings = Settings(bot)
        await settings.load()
        self.assertIsNone(bot.cfg.cr_api_key)
        await settings.set("cle_api", "  " + "A" * 24 + "  ")
        self.assertEqual(bot.cfg.cr_api_key, "A" * 24)
        self.assertEqual(bot.api.configured, ("A" * 24, "https://api.clashroyale.com/v1"))
        raw = await self.db.get_setting("cle_api")
        self.assertTrue(raw.startswith("enc:"))
        self.assertNotIn("A" * 24, raw)              # jamais la clé en clair en base

        bot2 = self.make_bot()                       # redémarrage : relu depuis la base
        await Settings(bot2).load()
        self.assertEqual(bot2.cfg.cr_api_key, "A" * 24)

    async def test_wrong_vault_key_fails_safely(self):
        bot = self.make_bot()
        await Settings(bot).load()
        await Settings(bot).set("cle_api", "B" * 24)
        bot2 = self.make_bot(token="autre-token")     # DISCORD_TOKEN changé -> vault différent
        await Settings(bot2).load()
        self.assertIsNone(bot2.cfg.cr_api_key)         # pas d'exception, pas de valeur illisible utilisée

    async def test_validation_and_clear(self):
        bot = self.make_bot()
        settings = Settings(bot)
        await settings.load()
        with self.assertRaises(SettingError):
            await settings.set("clan", "invalide !!")
        await settings.set("clan", "2pp")
        self.assertEqual(bot.cfg.clan_tag, "#2PP")
        await settings.clear("clan")
        self.assertIsNone(bot.cfg.clan_tag)

    async def test_env_fallback_then_db_override(self):
        os.environ["CLAN_TAG"] = "#2PP0V"
        try:
            bot = self.make_bot()
            await Settings(bot).load()
            self.assertEqual(bot.cfg.clan_tag, "#2PP0V")   # valeur initiale depuis .env
            await Settings(bot).set("clan", "#8CCLR")
            bot2 = self.make_bot()
            await Settings(bot2).load()
            self.assertEqual(bot2.cfg.clan_tag, "#8CCLR")   # la base prime désormais
        finally:
            del os.environ["CLAN_TAG"]


class ClanSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "c.db"))
        await self.db.connect()
        self.api = FakeAPI()
        self.sent = []
        self.bot = SimpleNamespace(
            api=self.api, db=self.db, members={}, clan_info={}, sync=FakeSync(),
            cfg=SimpleNamespace(ready=True, clan_tag=OWN, join_channel_id=1, log_channel_id=2,
                               war_channel_id=3, leave_channel_id=2),
        )
        self.tracker = Tracker(self.bot)

        async def fake_send(channel_id, **kw):
            self.sent.append((channel_id, kw))
            return None
        self.tracker.send = fake_send
        self.api.members = [member("#X1", "Alice")]

    async def asyncTearDown(self):
        await self.db.close()

    async def test_type_and_trophies_change_reported(self):
        self.api.clan_extra = {"type": "inviteOnly", "requiredTrophies": 4000, "name": "Nous"}
        await self.tracker.run_once()          # bootstrap : silencieux
        self.assertEqual(self.sent, [])

        self.api.clan_extra["type"] = "open"
        await self.tracker.run_once()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], 2)    # salon des logs
        self.assertIn("ouvert", self.sent[0][1]["embed"].title.lower())

        self.sent.clear()
        self.api.clan_extra["requiredTrophies"] = 6000
        await self.tracker.run_once()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("6", self.sent[0][1]["embed"].description)

        self.sent.clear()                        # rien de notable -> rien d'envoyé
        await self.tracker.run_once()
        self.assertEqual(self.sent, [])


class DailyReportSettingsTests(unittest.TestCase):
    def test_bool_kind(self):
        self.assertTrue(parse_setting("bool", "oui"))
        self.assertTrue(parse_setting("bool", "Activé"))
        self.assertFalse(parse_setting("bool", "non"))
        self.assertFalse(parse_setting("bool", "0"))
        with self.assertRaises(SettingError):
            parse_setting("bool", "peut-être")

    def test_heure_kind(self):
        from datetime import time
        self.assertEqual(parse_setting("heure", "09:00"), time(9, 0))
        self.assertEqual(parse_setting("heure", " 23:59 "), time(23, 59))
        with self.assertRaises(SettingError):
            parse_setting("heure", "25:00")
        with self.assertRaises(SettingError):
            parse_setting("heure", "pas une heure")

    def test_display(self):
        from settings import display
        self.assertEqual(display(BY_KEY["quotidien"], True), "✅ Activé")
        self.assertEqual(display(BY_KEY["quotidien"], False), "❌ Désactivé")
        from datetime import time
        self.assertEqual(display(BY_KEY["heure_quotidien"], time(9, 0)), "`09:00`")


class DailyReportEmbedTests(unittest.TestCase):
    def test_no_race(self):
        embed = build_daily_report_embed(None, OWN, [])
        self.assertIn("pas engagé", embed.description)

    def test_training_day(self):
        race = {"periodType": "training", "clans": [
            {"tag": OWN, "fame": 500}, {"tag": "#OTHER", "fame": 900}]}
        embed = build_daily_report_embed(race, OWN, [])
        self.assertIn("Entraînement", embed.title)
        self.assertEqual(embed.fields[0].value, "**#2**/2 — 500 pts")
        self.assertEqual(len(embed.fields), 1)   # pas de decks en entraînement

    def test_war_day_with_remaining_decks(self):
        race = {"periodType": "warDay", "clans": [{"tag": OWN, "fame": 1200}]}
        rows = [("Alice", "#A1", 1), ("Bob", "#B1", 3)]
        embed = build_daily_report_embed(race, OWN, rows)
        names = [f.name for f in embed.fields]
        self.assertIn("Decks restants aujourd'hui", names)
        self.assertIn("À relancer", names)
        self.assertIn("Alice", [f.value for f in embed.fields if f.name == "À relancer"][0])

    def test_war_day_all_done(self):
        race = {"periodType": "warDay", "clans": [{"tag": OWN, "fame": 1200}]}
        embed = build_daily_report_embed(race, OWN, [])
        value = [f.value for f in embed.fields if f.name == "À relancer"][0]
        self.assertIn("Tout le monde", value)


class DailyReportTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "d.db"))
        await self.db.connect()
        self.sent = []
        outer = self

        class Chan:
            async def send(self, **kw):
                outer.sent.append(kw)

        class API:
            async def current_river_race(self, tag, ttl=0):
                return {"clan": {"participants": []}, "periodType": "training", "clans": [{"tag": tag, "fame": 10}]}

            async def clan(self, tag, ttl=0):
                return {"memberList": []}

        self.bot = SimpleNamespace(
            api=API(), db=self.db,
            cfg=SimpleNamespace(ready=True, clan_tag=OWN, timezone="UTC", war_channel_id=3,
                               daily_report_enabled=True, daily_report_time=__import__("datetime").time(9, 0)),
            get_channel=lambda cid: Chan(),
        )
        self.war = War(self.bot)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_sends_once_per_day_in_window(self):
        import datetime as dt
        from unittest.mock import patch
        with patch("cogs.war.datetime") as mock_dt:
            mock_dt.now.return_value = dt.datetime(2026, 1, 1, 9, 2, tzinfo=dt.timezone.utc)
            await self.war._daily()
        self.assertEqual(len(self.sent), 1)
        with patch("cogs.war.datetime") as mock_dt:
            mock_dt.now.return_value = dt.datetime(2026, 1, 1, 9, 5, tzinfo=dt.timezone.utc)
            await self.war._daily()   # même jour, déjà envoyé
        self.assertEqual(len(self.sent), 1)

    async def test_disabled_sends_nothing(self):
        import datetime as dt
        from unittest.mock import patch
        self.bot.cfg.daily_report_enabled = False
        with patch("cogs.war.datetime") as mock_dt:
            mock_dt.now.return_value = dt.datetime(2026, 1, 1, 9, 2, tzinfo=dt.timezone.utc)
            await self.war._daily()
        self.assertEqual(self.sent, [])

    async def test_outside_window_sends_nothing(self):
        import datetime as dt
        from unittest.mock import patch
        with patch("cogs.war.datetime") as mock_dt:
            mock_dt.now.return_value = dt.datetime(2026, 1, 1, 14, 0, tzinfo=dt.timezone.utc)
            await self.war._daily()
        self.assertEqual(self.sent, [])


class WizardStepJumpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmp, "j.db"))
        await self.db.connect()

        class FakeAPI:
            def configure(self, key, url):
                pass

            async def get(self, path, params=None, ttl=0):
                return {"items": []}

            async def clan(self, tag, ttl=0):
                return {"tag": tag, "name": "Mon Clan", "members": 12}

        class FakeGuild:
            def __init__(self):
                self.id, self.name = 999, "Serveur Test"
                self.text_channels = [SimpleNamespace(id=111, name="arrivees")]
                self.roles = [SimpleNamespace(id=333, name="Staff")]

            def get_channel(self, cid):
                return next((c for c in self.text_channels if c.id == cid), None)

            def get_role(self, rid):
                return next((r for r in self.roles if r.id == rid), None)

        self.guild = FakeGuild()
        self.bot = SimpleNamespace(
            cfg=Config(discord_token="tok"), db=self.db, api=FakeAPI(), members={},
            get_guild=lambda gid: self.guild if gid == self.guild.id else None,
            dispatch=lambda *a, **k: None,
        )
        self.bot.settings = Settings(self.bot)
        await self.bot.settings.load()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_select_opens_requested_step_directly(self):
        setup_mod.start_wizard(1, self.guild.id)
        select = setup_mod.StepSelect(self.bot, 1)
        select._values = ["3"]
        it = FakeWizardInteraction(self.bot, 1)
        await select.callback(it)
        self.assertIsInstance(it.response.modal, setup_mod.Step3)

    async def test_submitted_step_marks_itself_current_in_menu(self):
        setup_mod.start_wizard(1, self.guild.id)
        step3 = setup_mod.Step3(self.bot)
        step3.staff._value = "Staff"
        step3.surveillance._value = "20"
        step3.fuseau._value = "Europe/Paris"
        it = FakeWizardInteraction(self.bot, 1)
        await step3.on_submit(it)
        view = it.followup.sent[0][1]["view"]
        select_item = next(c for c in view.children if isinstance(c, setup_mod.StepSelect))
        default_opt = next(o for o in select_item.options if o.default)
        self.assertEqual(default_opt.value, "3")
        self.assertEqual(self.bot.cfg.poll_interval, 20)

    async def test_can_jump_backwards_and_session_stays_open(self):
        setup_mod.start_wizard(1, self.guild.id)
        select = setup_mod.StepSelect(self.bot, 1)
        select._values = ["3"]
        await select.callback(FakeWizardInteraction(self.bot, 1))
        select._values = ["1"]
        it = FakeWizardInteraction(self.bot, 1)
        await select.callback(it)
        self.assertIsInstance(it.response.modal, setup_mod.Step1)
        self.assertIsNotNone(setup_mod.wizard_guild(self.bot, 1))   # touch_wizard a prolongé la session

    async def test_finishing_step4_keeps_session_open_for_more_edits(self):
        setup_mod.start_wizard(1, self.guild.id)
        step4 = setup_mod.Step4(self.bot)
        step4.rappels._value = ""
        step4.quotidien._value = ""
        step4.heure_quotidien._value = ""
        step4.liaison._value = ""
        step4.api_url._value = ""
        it = FakeWizardInteraction(self.bot, 1)
        await step4.on_submit(it)
        self.assertIsNotNone(setup_mod.wizard_guild(self.bot, 1))   # pas de end_wizard() prématuré
        final_view = it.followup.sent[-1][1]["view"]
        self.assertTrue(any(isinstance(c, setup_mod.StepSelect) for c in final_view.children))


class MinutesSettingTests(unittest.TestCase):
    def test_parse_and_display(self):
        from settings import display
        self.assertEqual(parse_setting("minutes", "60"), 60)
        self.assertEqual(display(BY_KEY["maj_intervalle"], 60), "60 min")
        with self.assertRaises(SettingError):
            parse_setting("minutes", "3")   # < 5
        with self.assertRaises(SettingError):
            parse_setting("minutes", "9999")   # > 1440
        with self.assertRaises(SettingError):
            parse_setting("minutes", "abc")


def write_fake_git(fake_bin: str, lines: list) -> None:
    """Écrit un faux exécutable `git` dans fake_bin à partir de lignes shell simples."""
    import stat
    script = os.path.join(fake_bin, "git")
    with open(script, "w") as f:
        f.write("\n".join(["#!/bin/sh"] + lines) + "\n")
    os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)


class UpdaterTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_a_git_repo(self):
        import tempfile
        import updater as updater_mod
        tmp = tempfile.mkdtemp()
        old_root = updater_mod.REPO_ROOT
        updater_mod.REPO_ROOT = tmp
        try:
            self.assertFalse(is_git_repo())
            self.assertIsNone(await behind_count(SimpleNamespace()))
        finally:
            updater_mod.REPO_ROOT = old_root

    async def test_behind_count_and_update_via_fake_git(self):
        # Simule `git` avec un faux exécutable pour ne dépendre d'aucun vrai dépôt.
        import tempfile
        import updater as updater_mod
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, ".git"))
        fake_bin = os.path.join(tmp, "fakebin")
        os.makedirs(fake_bin)
        write_fake_git(fake_bin, [
            'if [ "$1" = fetch ]; then exit 0; fi',
            'if [ "$1" = rev-list ]; then echo 3; exit 0; fi',
            'if [ "$1" = pull ]; then echo pulled; exit 0; fi',
            'exit 1',
        ])
        old_root, old_path = updater_mod.REPO_ROOT, os.environ.get("PATH", "")
        updater_mod.REPO_ROOT = tmp
        os.environ["PATH"] = fake_bin + os.pathsep + old_path
        try:
            self.assertTrue(is_git_repo())
            self.assertEqual(await behind_count(SimpleNamespace()), 3)

            closed = []

            class FakeBot:
                async def close(self):
                    closed.append(True)

            async def noop_sleep(*_a, **_k):
                return None

            # empêche le vrai redémarrage (os.execv) et le vrai pip (sys.prefix bidon -> pip introuvable)
            with unittest.mock.patch("os.execv") as fake_execv, \
                 unittest.mock.patch("asyncio.sleep", new=noop_sleep), \
                 unittest.mock.patch("sys.prefix", tmp):
                result = await apply_update(FakeBot())
            self.assertTrue(result)
            self.assertEqual(closed, [True])
            fake_execv.assert_called_once()
        finally:
            updater_mod.REPO_ROOT = old_root
            os.environ["PATH"] = old_path

    async def test_apply_update_aborts_on_pull_failure(self):
        import tempfile
        import updater as updater_mod
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, ".git"))
        fake_bin = os.path.join(tmp, "fakebin")
        os.makedirs(fake_bin)
        write_fake_git(fake_bin, [
            'if [ "$1" = pull ]; then echo conflict; exit 1; fi',
            'exit 0',
        ])
        old_root, old_path = updater_mod.REPO_ROOT, os.environ.get("PATH", "")
        updater_mod.REPO_ROOT = tmp
        os.environ["PATH"] = fake_bin + os.pathsep + old_path
        try:
            with unittest.mock.patch("os.execv") as fake_execv:
                result = await apply_update(SimpleNamespace())
            self.assertFalse(result)
            fake_execv.assert_not_called()
        finally:
            updater_mod.REPO_ROOT = old_root
            os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
