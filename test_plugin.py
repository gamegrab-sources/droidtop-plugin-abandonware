"""Drives the abandonware plugin through search, detail, acquire and settings with a fake droidtop module (the real
one only exists inside droidtop's embedded interpreter): `python3 test_plugin.py` from this folder. CI runs the same.

The MyAbandonware pages in fixtures/ are trimmed copies of what the site served on 2026-10-09 (a listing's game cards,
a game page's download sections, the browser-check page a search request got). The Internet Archive answers are built
by hand in the documented shape of its advancedsearch and metadata APIs."""
import base64
import json
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as handle:
        return handle.read()


calls = []
store = {}
answers = {}
session = {"signedIn": False}


def fake_call(api, op, args=None, version=1):
    args = args or {}
    calls.append((api, op, args))
    if api == "data" and op == "write":
        raw = base64.b64decode(args["base64"])
        store[args["name"]] = (store.get(args["name"], b"") if args.get("append") else b"") + raw
        return {"ok": True, "data": {}}
    if api == "data" and op == "read":
        if args["name"] not in store:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "no such file"}}
        raw = store[args["name"]]
        part = raw[args["offset"] : args["offset"] + args["length"]]
        return {"ok": True, "data": {"base64": base64.b64encode(part).decode("ascii"), "eof": args["offset"] + len(part) >= len(raw)}}
    if api == "web.session" and op == "status":
        return {"ok": True, "data": {"signedIn": session["signedIn"]}}
    if api in ("net", "web.session") and op in ("http", "fetch"):
        key = (args["method"], args["url"])
        for prefixed in [k for k in answers if k[0] == args["method"] and k[1].endswith("*") and args["url"].startswith(k[1][:-1])]:
            key = prefixed
        if key not in answers:
            return {"ok": False, "error": {"code": "PERMISSION_DENIED", "message": "not reachable in this test: %s %s" % key}}
        answer = answers[key]
        if "error" in answer:
            return {"ok": False, "error": answer["error"]}
        return {"ok": True, "data": dict(answer)}
    if api == "web.session" and op in ("open_in_session", "sign_in", "clear"):
        return {"ok": True, "data": answers.get((api, op), {})}
    return {"ok": False, "error": {"code": "UNSUPPORTED", "message": "%s %s" % (api, op)}}


fake_host = types.ModuleType("droidtop.host")
fake_host.call = fake_call
fake_pkg = types.ModuleType("droidtop")
fake_pkg.__path__ = []
fake_pkg.host = fake_host
sys.modules["droidtop"] = fake_pkg
sys.modules["droidtop.host"] = fake_host

sys.path.insert(0, os.path.join(HERE, "src"))
import plugin  # noqa: E402


def send(point, op, **args):
    return json.loads(plugin.handle(json.dumps({"point": point, "version": 1, "op": op, "args": args})))


def job(op, point="library.sources", **args):
    return json.loads(plugin.start_job("j1", {"point": point, "op": op, "args": args}, lambda percent, status: None))


def page(body, url="x", status=200, headers=None):
    return {"status": status, "url": url, "headers": headers or {}, "body": body}


def items(view):
    return view["sections"][0]["items"]


SEARCH = "https://www.myabandonware.com/search/q/pro%20race"
GAME = "https://www.myabandonware.com/game/prince-of-persia-pd"
FILE = "https://www.myabandonware.com/download/m4og-prince-of-persia"
IA_QUERY_START = "https://archive.org/advancedsearch.php?"


class MyAbandonwareParsingTest(unittest.TestCase):
    def test_cards_give_slug_title_platforms_and_year(self):
        cards = plugin.parse_cards(fixture("ma_cards.html"))
        self.assertEqual(4, len(cards))
        first = cards[0]
        self.assertEqual("pro-race-driver-eof", first["slug"])
        self.assertEqual("Pro Race Driver", first["title"])
        self.assertEqual("Win", first["platforms"])
        self.assertEqual("2003", first["year"])
        for card in cards:
            self.assertRegex(card["slug"], plugin.SLUG)

    def test_a_game_page_gives_downloads_per_platform_and_skips_manuals(self):
        game = plugin.parse_game(fixture("ma_game.html"))
        self.assertEqual("Prince of Persia", game["title"])
        self.assertIn("Released in 1990 on DOS", game["summary"])
        names = [s["name"] for s in game["sections"]]
        self.assertEqual(["DOS Version", "Mac Version"], names)
        dos = game["sections"][0]["files"]
        self.assertEqual(5, len(dos), "five downloads and the manual is left out")
        self.assertEqual("/download/lg9y-prince-of-persia", dos[0]["path"])
        self.assertEqual("v1.0", dos[0]["version"])
        self.assertEqual("English", dos[0]["language"])
        self.assertEqual("337 KB", dos[0]["size"])
        mac = game["sections"][1]["files"]
        self.assertEqual(1, len(mac))
        self.assertEqual("1 MB", mac[0]["size"])
        self.assertEqual("", mac[0]["version"], "the button's own word is not a version")

    def test_the_browser_check_is_recognised(self):
        self.assertTrue(plugin._is_challenge(fixture("ma_challenge.html")))
        self.assertFalse(plugin._is_challenge(fixture("ma_game.html")))


class PluginTest(unittest.TestCase):
    def setUp(self):
        calls.clear()
        store.clear()
        answers.clear()
        session["signedIn"] = False

    # ---------------------------------------------------------------- search

    def test_search_returns_both_sites_with_refs(self):
        answers[("GET", SEARCH)] = page(fixture("ma_cards.html"))
        ia = {"response": {"docs": [{"identifier": "msdos_Pro_Race_Driver_1999", "title": "Pro Race Demo", "year": "1999"}, {"identifier": "bad id!", "title": "x"}]}}
        answers[("GET", IA_QUERY_START + "*")] = page(json.dumps(ia))
        reply = send("library.sources", "search", query="pro race", values={})
        results = reply["data"]["results"]
        self.assertEqual("ma:pro-race-driver-eof", results[0]["id"])
        self.assertEqual({"site": "ma", "slug": "pro-race-driver-eof"}, results[0]["ref"])
        self.assertEqual("windows", results[0]["platform"])
        self.assertEqual(["Win", "2003"], results[0]["columns"])
        ia_results = [r for r in results if r["id"].startswith("ia:")]
        self.assertEqual(["ia:msdos_Pro_Race_Driver_1999"], [r["id"] for r in ia_results], "an item name that is not an Internet Archive id is dropped")
        # The IA query names the collections and mediatype in Lucene, and asks for JSON.
        query = [c for c in calls if c[0] == "net" and c[2]["url"].startswith(IA_QUERY_START)][0][2]["url"]
        self.assertIn("collection%3Asoftwarelibrary_msdos_games", query)
        self.assertIn("output=json", query)

    def test_a_system_filters_the_cards_to_its_own_platform(self):
        answers[("GET", SEARCH)] = page(fixture("ma_cards.html"))
        reply = send("library.sources", "search", query="pro race", values={"site": "ma"}, context={"system": {"id": "dos", "name": "DOS"}})
        self.assertEqual([], reply["data"]["results"], "none of the fixture's cards is a DOS game")
        reply = send("library.sources", "search", query="pro race", values={"site": "ma"}, context={"system": {"id": "windows", "name": "Windows"}})
        self.assertTrue(all(r["platform"] == "windows" for r in reply["data"]["results"]))
        self.assertTrue(reply["data"]["results"])

    def test_the_browser_check_is_reported_in_plain_words_and_never_worked_around(self):
        answers[("GET", SEARCH)] = page(fixture("ma_challenge.html"))
        reply = send("library.sources", "search", query="pro race", values={"site": "ma"})
        self.assertEqual("FAILED", reply["error"]["code"])
        self.assertIn("Open MyAbandonware", reply["error"]["message"])
        # One request, no retry, nothing but the page asked for.
        self.assertEqual(1, len([c for c in calls if c[1] in ("http", "fetch")]))

    def test_with_the_browser_session_the_request_goes_through_it(self):
        session["signedIn"] = True
        answers[("GET", SEARCH)] = page(fixture("ma_cards.html"))
        send("library.sources", "search", query="pro race", values={"site": "ma"})
        self.assertEqual("web.session", [c for c in calls if c[1] in ("http", "fetch")][0][0])

    def test_nothing_typed_is_nothing_found_and_a_site_can_be_off(self):
        self.assertEqual([], send("library.sources", "search", query="  ", values={})["data"]["results"])
        send("ui.settings", "setSite", values={"ma": "false", "ia": "false"})
        self.assertEqual([], send("library.sources", "search", query="x", values={})["data"]["results"])

    # ---------------------------------------------------------------- detail

    def test_detail_of_a_game_lists_every_download_as_one_choice(self):
        answers[("GET", GAME)] = page(fixture("ma_game.html"))
        reply = send("library.sources", "detail", ref={"site": "ma", "slug": "prince-of-persia-pd"})
        rows = {r["id"]: r for r in items(reply["data"])}
        options = rows["file"]["options"]
        self.assertEqual(6, len(options))
        self.assertEqual("DOS Version · v1.0 · English · 337 KB", options[0]["label"])
        self.assertEqual("/download/lg9y-prince-of-persia", options[0]["value"])
        self.assertEqual("job", rows["acquire"]["action"]["kind"])
        self.assertEqual({"ref": {"site": "ma", "slug": "prince-of-persia-pd"}}, rows["acquire"]["action"]["args"])

    def test_detail_of_an_item_lists_its_game_files_only(self):
        meta = {"result": {"title": "Pro Race Demo", "year": "1999", "publisher": "Codemasters", "description": "<p>A racing demo.</p>", "collection": ["softwarelibrary_msdos_games"]}}
        files = {
            "result": [
                {"name": "Pro Race.zip", "source": "original", "size": "2048", "format": "ZIP"},
                {"name": "Pro Race_meta.xml", "source": "original", "size": "10"},
                {"name": "__ia_thumb.jpg", "source": "original", "size": "10"},
                {"name": "Pro Race.mp4", "source": "derivative", "size": "10"},
            ]
        }
        answers[("GET", "https://archive.org/metadata/msdos_Pro_Race_1999/metadata")] = page(json.dumps(meta))
        answers[("GET", "https://archive.org/metadata/msdos_Pro_Race_1999/files")] = page(json.dumps(files))
        reply = send("library.sources", "detail", ref={"site": "ia", "id": "msdos_Pro_Race_1999"})
        rows = {r["id"]: r for r in items(reply["data"])}
        self.assertEqual("1999", rows["year"]["value"])
        self.assertEqual("A racing demo.", rows["about"]["subtitle"])
        self.assertEqual([{"value": "Pro Race.zip", "label": "Pro Race.zip · 2.0 KB"}], rows["file"]["options"])

    def test_detail_names_the_browser_check_too(self):
        answers[("GET", GAME)] = page(fixture("ma_challenge.html"))
        reply = send("library.sources", "detail", ref={"site": "ma", "slug": "prince-of-persia-pd"})
        self.assertIn("Open MyAbandonware", reply["error"]["message"])

    def test_a_bad_ref_is_an_error_reply(self):
        self.assertEqual("INVALID_ARGS", send("library.sources", "detail", ref={"site": "ma", "slug": "../x"})["error"]["code"])
        self.assertEqual("NOT_FOUND", send("library.sources", "detail", ref={"site": "zz"})["error"]["code"])

    # --------------------------------------------------------------- acquire

    def test_a_myabandonware_download_goes_by_the_download_address_with_a_cookie_and_the_referer(self):
        answers[("GET", GAME)] = page(fixture("ma_game.html"), headers={"set-cookie": "PHPSESSID=abc123def; expires=x; path=/"})
        answers[("HEAD", FILE)] = page("", url="https://d2.xp.myabandonware.com/t/1111/Prince-of-Persia_DOS_EN_v11.zip", headers={"content-type": "application/zip", "content-length": "355000"})
        reply = job("acquire", ref={"site": "ma", "slug": "prince-of-persia-pd"}, values={"file": "/download/m4og-prince-of-persia"})
        self.assertTrue(reply["ok"], reply)
        download = json.loads(reply["values"]["download"])
        self.assertEqual(FILE, download["url"])
        self.assertEqual("Prince-of-Persia_DOS_EN_v11.zip", download["fileName"])
        self.assertEqual({"Referer": GAME, "Cookie": "PHPSESSID=abc123def"}, download["headers"])
        self.assertEqual(355000, download["size"])
        self.assertEqual("archive", download["unpack"], "a zip is unpacked by droidtop into a folder")
        self.assertEqual("dos", reply["values"]["system"])
        self.assertNotIn("engine", reply["values"], "droidtop's engine list has no DOS entry")

    def test_a_download_the_site_will_not_give_falls_back_to_the_browser_view_when_there_is_one(self):
        answers[("GET", GAME)] = page(fixture("ma_game.html"))
        answers[("HEAD", FILE)] = page("<html>", url=GAME, headers={"content-type": "text/html"})
        reply = job("acquire", ref={"site": "ma", "slug": "prince-of-persia-pd"}, values={"file": "/download/m4og-prince-of-persia"})
        self.assertIn("Open MyAbandonware", reply["error"])
        session["signedIn"] = True
        answers[("GET", GAME)] = page(fixture("ma_game.html"))  # fetched through the session now
        answers[("web.session", "open_in_session")] = {"captured": True, "download": {"url": "https://d2.xp.myabandonware.com/t/9/P.zip", "fileName": "P.zip", "session": "w-" + "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12}}
        answers[("HEAD", FILE)] = page("<html>", url=GAME, headers={"content-type": "text/html"})
        reply = job("acquire", ref={"site": "ma", "slug": "prince-of-persia-pd"}, values={"file": "/download/m4og-prince-of-persia"})
        self.assertTrue(reply["ok"], reply)
        captured = json.loads(reply["values"]["download"])
        self.assertEqual("P.zip", captured["fileName"])
        self.assertNotIn("unpack", captured, "a captured download is passed on as droidtop made it")

    def test_an_internet_archive_download_names_its_file_and_system(self):
        meta = {"result": {"title": "Pro Race Demo", "collection": ["softwarelibrary_msdos_games", "softwarelibrary_msdos"]}}
        files = {"result": [{"name": "Pro Race.zip", "source": "original", "size": "2048"}]}
        answers[("GET", "https://archive.org/metadata/msdos_Pro_Race_1999/metadata")] = page(json.dumps(meta))
        answers[("GET", "https://archive.org/metadata/msdos_Pro_Race_1999/files")] = page(json.dumps(files))
        reply = job("acquire", ref={"site": "ia", "id": "msdos_Pro_Race_1999"}, values={"file": "Pro Race.zip"})
        download = json.loads(reply["values"]["download"])
        self.assertEqual("https://archive.org/download/msdos_Pro_Race_1999/Pro%20Race.zip", download["url"])
        self.assertEqual("Pro Race.zip", download["fileName"], "kept as the source names it")
        self.assertEqual("archive", download["unpack"])
        self.assertEqual(2048, download["size"])
        self.assertEqual("dos", reply["values"]["system"])

    # -------------------------------------------------------------- settings

    def test_settings_view_and_the_actions_it_names(self):
        view = send("ui.settings", "view")["data"]
        ids = [r["id"] for r in items(view)]
        self.assertEqual(["about", "ma", "ia", "collections", "browser", "open"], ids)
        self.assertTrue(send("ui.settings", "setCollections", values={"collections": "https://archive.org/details/softwarelibrary_win95, bad!"})["ok"])
        self.assertEqual(["softwarelibrary_win95"], plugin._state()["collections"])
        self.assertTrue(send("ui.settings", "setCollections", values={"collections": ""})["ok"])
        self.assertEqual(plugin.DEFAULT_COLLECTIONS, plugin._state()["collections"])

    def test_opening_the_site_asks_droidtops_browser_view_and_clearing_is_offered_after(self):
        answers[("web.session", "sign_in")] = {"signedIn": True}
        reply = job("openSite", point="ui.settings")
        self.assertTrue(reply["ok"])
        self.assertEqual(("web.session", "sign_in", {"url": "https://www.myabandonware.com/"}), [c for c in calls if c[1] == "sign_in"][0])
        session["signedIn"] = True
        self.assertIn("clear", [r["id"] for r in items(send("ui.settings", "view")["data"])])

    def test_unknown_things_are_error_replies(self):
        self.assertEqual("UNSUPPORTED", send("library.sources", "lookup")["error"]["code"])
        self.assertFalse(job("nothing", point="ui.panel")["ok"])
        self.assertEqual("INVALID_ARGS", json.loads(plugin.handle("nope"))["error"]["code"])


class ManifestTest(unittest.TestCase):
    def test_the_manifest_declares_what_the_plugin_uses_and_nothing_more(self):
        with open(os.path.join(HERE, "manifest.template.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual("gamegrab.abandonware", manifest["id"])
        self.assertEqual("python", manifest["kind"])
        self.assertFalse(manifest.get("requestsRoot"))
        self.assertEqual({"library.sources", "ui.settings"}, set(p["point"] for p in manifest["provides"]))
        permissions = dict((p["id"], p) for p in manifest["permissions"])
        self.assertEqual({"net.domains", "web.session", "library.folders.write"}, set(permissions))
        domains = permissions["net.domains"]["domains"]
        for host in ("www.myabandonware.com", "archive.org", "*.archive.org", "*.myabandonware.com"):
            self.assertIn(host, domains)
        self.assertEqual(["myabandonware.com"], permissions["web.session"]["domains"])
        with open(os.path.join(HERE, "src", "plugin.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotRegex(text, r"^\s*(import|from) (socket|urllib\.request|http|ssl)\b", "a contained plugin has no sockets")


if __name__ == "__main__":
    unittest.main()
