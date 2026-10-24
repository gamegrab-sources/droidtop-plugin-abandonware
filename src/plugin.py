"""gamegrab.abandonware: search and download abandonware from MyAbandonware and the Internet Archive's software library.

An unofficial droidtop plugin (gamegrab-sources, GPL-3.0). Python kind, standard library only, run contained
(docs/plugin-api.md 5.3 in droidtop): requests go through droidtop's net.http (or the signed-in browser view's
web.session fetch), settings through the data API, and the files themselves are downloaded by droidtop (the acquire
reply's `download`).

Through the `library.sources` point (search, detail, acquire) it offers:

  MyAbandonware        search /search/q/<words>; a game page lists a download per platform and version
  Internet Archive     advancedsearch over the software-library collections (MS-DOS and Windows 3.x games by
                       default, more in Settings), an item's files from its metadata

MyAbandonware puts its search behind a "cake test" (a JavaScript and captcha browser check). This plugin does not try
to pass it: when a request meets the check it says so, and Settings > "Open MyAbandonware" opens the site in droidtop's
own browser view, where the person passes it as in any browser; the plugin then asks through that session (web.session
fetch). The same view is the fallback for a download the site will not hand to a plain request (open_in_session).

What the plugin tells droidtop about a download besides the file: the system the game is for (`system`, droidtop's id
for DOS, Windows, the Mac, the Amiga and the rest), because the engine hint (`engine`) only names Enginehost's
engines and droidtop has no DOS or Windows entry in it.
"""
import base64
import html
import json
import re
import urllib.parse

import droidtop.host

PLUGIN_ID = "gamegrab.abandonware"

MA = "https://www.myabandonware.com"
IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/"
IA_DOWNLOAD = "https://archive.org/download/"
LOGIN_URL = MA + "/"

STATE_FILE = "settings.json"
# data.read answers in base64 inside a JSON string, which crosses droidtop's broker as UTF-16: 120 KiB
# came back as 320 KB binder replies (logcat, BlueStacks, 2026-10-14). 48 KiB keeps a reply near 128 KB.
CHUNK = 48 * 1024
DEFAULT_COLLECTIONS = ["softwarelibrary_msdos_games", "softwarelibrary_win3"]
IA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,99}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,120}$")
DOWNLOAD_PATH = re.compile(r"^/download/[A-Za-z0-9-]{3,120}$")

# MyAbandonware's platform names -> droidtop's system ids (the ES-DE short names, droidtop-platforms' platforms).
PLATFORM_SYSTEMS = {
    "dos": "dos",
    "win": "windows",
    "windows": "windows",
    "windows 3.x": "windows3x",
    "mac": "macintosh",
    "amiga": "amiga",
    "atari st": "atarist",
    "apple ii": "apple2",
    "amstrad cpc": "amstradcpc",
    "c64": "c64",
    "commodore 64": "c64",
    "zx spectrum": "zxspectrum",
    "pc-98": "pc98",
    "fm towns": "fmtowns",
    "sam coupé": "samcoupe",
    "genesis": "genesis",
    "sega cd": "segacd",
    "sega master system": "mastersystem",
    "game gear": "gamegear",
    "turbografx cd": "pcenginecd",
    "sharp x68000": "x68000",
    "msx": "msx",
}
# What a system droidtop is browsing means in each place.
SYSTEM_PLATFORMS = {
    "dos": ["dos"],
    "windows": ["win", "windows"],
    "windows3x": ["win", "windows"],
    "windows9x": ["win", "windows"],
    "pc": ["dos", "win", "windows"],
}
SYSTEM_COLLECTIONS = {
    "dos": ["softwarelibrary_msdos_games"],
    "windows3x": ["softwarelibrary_win3"],
}
# IA files that are not the game: its own bookkeeping, previews and derived copies.
IA_SKIP = re.compile(r"(_meta\.xml|_files\.xml|_reviews\.xml|_archive\.torrent|__ia_thumb\.jpg|\.(png|jpe?g|gif|mp4|ogv|ogg|mp3|xml|sqlite|txt|json|torrent))$", re.I)


# droidtop's closed set of error codes (docs/plugin-api.md 1.3).
ERROR_CODES = {"INVALID_ARGS", "UNSUPPORTED", "NOT_FOUND", "PERMISSION_DENIED", "RATE_LIMITED", "TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_CRASHED", "CANCELLED", "FAILED"}


class HostError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


class Challenge(Exception):
    """MyAbandonware answered with its browser check instead of the page."""


# -------------------------------------------------------------------------------------------------- host helpers


def _ok(data=None):
    return json.dumps({"ok": True, "data": data if data is not None else {}})


def _error(code, message):
    return json.dumps({"ok": False, "error": {"code": code, "message": message}})


def _call(api, op, args=None, version=1):
    return droidtop.host.call(api, op, args or {}, version)


def _data(api, op, args):
    reply = _call(api, op, args)
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise HostError(error.get("code", "FAILED"), error.get("message", "%s %s failed" % (api, op)))
    return reply.get("data") or {}


def _http_error(site, status):
    """A site's HTTP failure in plain words (the line droidtop shows under the source)."""
    if status in (401, 403):
        return "%s refused the request. Try again later." % site
    if status in (404, 410):
        return "%s no longer has that page." % site
    if status == 429:
        return "%s asks to slow down. Try again in a minute." % site
    if status >= 500 or status == 0:
        return "%s is not answering right now. Try again later." % site
    return "%s sent an error (%d). Try again later." % (site, status)


UNREADABLE_IA = "The Internet Archive sent a page this plugin could not read. Try again later."


def _signed_in():
    try:
        return bool(_data("web.session", "get_status", {}).get("signedIn"))
    except HostError:
        return False


def _fetch(url, method="GET", headers=None, session=None):
    """(status, final url, headers, body). With a browser-view session when there is one, else a plain request."""
    use_session = _signed_in() if session is None else session
    args = {"url": url, "method": method, "headers": headers or {}, "as": "text", "timeoutMs": 25000}
    data = _data("web.session" if use_session else "net", "fetch" if use_session else "request", args)
    return int(data.get("status") or 0), data.get("url") or url, data.get("headers") or {}, data.get("body") or ""


def _read_json(name):
    data = bytearray()
    while True:
        reply = _call("data", "read", {"name": name, "offset": len(data), "length": CHUNK, "as": "base64"})
        if not reply.get("ok"):
            return None
        body = reply.get("data") or {}
        chunk = base64.b64decode(body.get("base64") or "")
        data.extend(chunk)
        if body.get("eof") or not chunk:
            break
    try:
        return json.loads(data.decode("utf-8")) if data else None
    except ValueError:
        return None


def _write_json(name, value):
    raw = json.dumps(value, sort_keys=True).encode("utf-8")
    offset = 0
    first = True
    while True:
        end = min(offset + CHUNK, len(raw))
        _data("data", "write", {"name": name, "base64": base64.b64encode(raw[offset:end]).decode("ascii"), "append": not first})
        first = False
        offset = end
        if offset >= len(raw):
            break


def _state():
    state = {"ma": True, "ia": True, "collections": list(DEFAULT_COLLECTIONS)}
    saved = _read_json(STATE_FILE)
    if isinstance(saved, dict):
        for key in ("ma", "ia"):
            if isinstance(saved.get(key), bool):
                state[key] = saved[key]
        if isinstance(saved.get("collections"), list):
            state["collections"] = [c for c in saved["collections"] if isinstance(c, str) and IA_ID.match(c)] or list(DEFAULT_COLLECTIONS)
    return state


# ---------------------------------------------------------------------------------------------- text helpers


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _size_text(size):
    value = float(size or 0)
    if value <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%d %s" % (value, unit)) if unit == "B" else ("%.1f %s" % (value, unit))
        value /= 1024.0
    return ""


def _is_challenge(body):
    head = body[:30000]
    return "jschl-title" in head or "<title>Please wait" in head or "Cake Test" in head


ARCHIVE_NAME = re.compile(r"\.(zip|7z|rar)$", re.I)


def _file_name(raw, fallback):
    """The name as the source gives it (droidtop keeps spaces and accents); only what no file system takes is replaced."""
    name = urllib.parse.unquote(raw or "").rsplit("/", 1)[-1].strip()
    name = re.sub(r'[:*?"<>|\x00-\x1f\\]', "_", name).lstrip(".")
    return name[:180] if name else fallback


def _unpacking(download):
    """A zip, 7z or rar is unpacked by droidtop into a folder of its own as the last step of the same job."""
    if ARCHIVE_NAME.search(download.get("fileName", "")):
        download["unpack"] = "archive"
    return download


# ----------------------------------------------------------------------------------------------- MyAbandonware


def parse_cards(page):
    """The game cards of a MyAbandonware listing or search page: slug, title, platforms, year."""
    cards = []
    anchors = list(re.finditer(r"<a\b([^>]*)>(.*?)</a>", page, re.S))
    names = []
    for match in anchors:
        attrs = match.group(1)
        if "c-item-game__name" not in attrs:
            continue
        href = re.search(r'href="(/game/[^"?#]+)"', attrs)
        if href:
            names.append((match, href.group(1)))
    for position, (match, href) in enumerate(names):
        end = names[position + 1][0].start() if position + 1 < len(names) else len(page)
        tail = page[match.end() : end]
        platforms = re.search(r'c-item-game__platforms">(.*?)</span>', tail, re.S)
        year = re.search(r'c-item-game__year">(.*?)</span>', tail, re.S)
        slug = href[len("/game/") :].strip("/")
        if not SLUG.match(slug):
            continue
        cards.append({"slug": slug, "title": _text(match.group(2)), "platforms": _text(platforms.group(1)) if platforms else "", "year": _text(year.group(1)) if year else ""})
    return cards


def parse_game(page):
    """A MyAbandonware game page: title, summary, and the downloads per platform section."""
    title = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    title = re.sub(r"^Download\s+", "", _text(title.group(1))) if title else ""
    summary = re.search(r'<meta name="description" content="(.*?)"\s*>', page, re.S)
    sections = []
    heads = list(re.finditer(r'<h4 class="platformDownload"[^>]*id="([^"]*)"[^>]*>(.*?)</h4>', page, re.S))
    for position, head in enumerate(heads):
        end = heads[position + 1].start() if position + 1 < len(heads) else len(page)
        body = page[head.end() : end]
        files = []
        for button in re.finditer(r'<a\b([^>]*class="[^"]*\bbutton\b[^"]*\bdownload\b[^"]*"[^>]*)>(.*?)</a>(\s*<span class="c-download__text">(.*?)</span>)?', body, re.S):
            href = re.search(r'href="([^"]+)"', button.group(1))
            if not href or not DOWNLOAD_PATH.match(href.group(1)):
                continue
            label = button.group(4) or button.group(2)
            language = re.search(r"alt='([^']*?) version'", label or "")
            size = re.search(r"([\d.,]+\s*[KMG]?B)\s*$", _text(label or ""))
            version = re.sub(r"^Download\s*", "", re.sub(r"[\d.,]+\s*[KMG]?B\s*$", "", _text(label or "")).strip())
            files.append({"path": href.group(1), "version": version, "language": language.group(1) if language else "", "size": size.group(1) if size else ""})
        if files:
            name = _text(head.group(2))
            sections.append({"id": html.unescape(head.group(1)), "name": name, "files": files})
    return {"title": title, "summary": html.unescape(summary.group(1)).strip() if summary else "", "sections": sections}


def system_for_platform(name):
    return PLATFORM_SYSTEMS.get((name or "").strip().lower(), "")


def _wanted_platform(card_platforms, context_system):
    keys = SYSTEM_PLATFORMS.get(context_system)
    if keys is None:
        keys = [k for k, v in PLATFORM_SYSTEMS.items() if v == context_system]
    if not context_system or not keys:
        return True
    names = [p.strip().lower() for p in re.split(r"[,/]", card_platforms)]
    return any(n in keys for n in names)


def _ma_search(query, context_system):
    url = MA + "/search/q/" + urllib.parse.quote(query)
    status, _final, _headers, body = _fetch(url)
    if _is_challenge(body):
        raise Challenge()
    if status != 200:
        raise HostError("FAILED", _http_error("MyAbandonware", status))
    return [_ma_row(card) for card in parse_cards(body) if _wanted_platform(card["platforms"], context_system)]


def _ma_row(card):
    first = card["platforms"].split(",")[0].strip() if card["platforms"] else ""
    return {
        "id": "ma:" + card["slug"],
        "title": card["title"],
        "subtitle": "MyAbandonware",
        "columns": [c for c in (card["platforms"], card["year"]) if c],
        "badges": [],
        "platform": system_for_platform(first),
        "ref": {"site": "ma", "slug": card["slug"]},
    }


def _ma_recent(context_system):
    """The games MyAbandonware added last, newest first, as its /browse/recent/ page lists them."""
    status, _final, _headers, body = _fetch(MA + "/browse/recent/")
    if _is_challenge(body):
        raise Challenge()
    if status != 200:
        raise HostError("FAILED", _http_error("MyAbandonware", status))
    return [_ma_row(card) for card in parse_cards(body) if _wanted_platform(card["platforms"], context_system)]


def _ma_game(slug):
    if not SLUG.match(slug or ""):
        raise HostError("INVALID_ARGS", "Missing game")
    status, _final, headers, body = _fetch(MA + "/game/" + slug)
    if _is_challenge(body):
        raise Challenge()
    if status == 404:
        raise HostError("NOT_FOUND", "MyAbandonware no longer has that game.")
    if status != 200:
        raise HostError("FAILED", _http_error("MyAbandonware", status))
    return parse_game(body), headers


# --------------------------------------------------------------------------------------------- Internet Archive


def _ia_collections(state, context_system):
    chosen = SYSTEM_COLLECTIONS.get(context_system)
    if chosen:
        return [c for c in chosen if c in state["collections"]] or chosen
    return state["collections"]


def _ia_search(query, state, context_system):
    # Lucene syntax: each word must be in the title, inside the chosen collections. Quote and escape the words.
    words = [re.sub(r'["\\]', "", w) for w in query.split() if w]
    if not words:
        return []
    title = " AND ".join('title:"%s"' % w for w in words)
    collections = " OR ".join("collection:%s" % c for c in _ia_collections(state, context_system))
    params = [
        ("q", "(%s) AND (%s) AND mediatype:software" % (title, collections)),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "year"),
        ("rows", "25"),
        ("sort[]", "downloads desc"),
        ("output", "json"),
    ]
    status, _final, _headers, body = _fetch(IA_SEARCH + "?" + urllib.parse.urlencode(params), session=False)
    if status != 200:
        raise HostError("FAILED", _http_error("The Internet Archive", status))
    try:
        docs = (json.loads(body).get("response") or {}).get("docs") or []
    except ValueError:
        raise HostError("FAILED", UNREADABLE_IA)
    results = []
    for doc in docs:
        identifier = doc.get("identifier")
        if not isinstance(identifier, str) or not IA_ID.match(identifier):
            continue
        year = str(doc.get("year") or "")
        results.append(
            {
                "id": "ia:" + identifier,
                "title": str(doc.get("title") or identifier)[:200],
                "subtitle": "Internet Archive",
                "columns": [year] if year else [],
                "badges": [],
                "platform": context_system or "",
                "ref": {"site": "ia", "id": identifier},
            }
        )
    return results


def _ia_item(identifier):
    if not IA_ID.match(identifier or ""):
        raise HostError("INVALID_ARGS", "Missing item")
    status, _f, _h, body = _fetch(IA_META + urllib.parse.quote(identifier) + "/metadata", session=False)
    if status != 200:
        raise HostError("NOT_FOUND" if status == 404 else "FAILED", _http_error("The Internet Archive", status))
    try:
        meta = json.loads(body).get("result") or {}
    except ValueError:
        raise HostError("FAILED", UNREADABLE_IA)
    status, _f, _h, body = _fetch(IA_META + urllib.parse.quote(identifier) + "/files", session=False)
    if status != 200:
        raise HostError("FAILED", _http_error("The Internet Archive", status))
    try:
        rows = json.loads(body).get("result") or []
    except ValueError:
        raise HostError("FAILED", UNREADABLE_IA)
    files = []
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if not name or "/" in name or row.get("source") != "original" or IA_SKIP.search(name):
            continue
        files.append({"name": name, "size": int(row.get("size") or 0), "format": row.get("format") or ""})
    return meta, files


# ------------------------------------------------------------------------------------------------------ views


def _view(title, items, subtitle=None):
    view = {"view": 1, "title": title, "sections": [{"id": "main", "items": items}]}
    if subtitle:
        view["subtitle"] = subtitle
    return view


def _info(item_id, title, value=None, subtitle=None):
    node = {"type": "info", "id": item_id, "title": title}
    if value:
        node["value"] = value
    if subtitle:
        node["subtitle"] = subtitle
    return node


CHALLENGE_TEXT = "MyAbandonware wants a browser check. Open MyAbandonware in this plugin's Settings once, then try again."


def _form():
    return _view(
        "Search abandonware",
        [
            {"type": "text", "id": "query", "title": "Search", "value": ""},
            {
                "type": "choice",
                "id": "site",
                "title": "Where",
                "options": [{"value": "all", "label": "MyAbandonware and the Internet Archive"}, {"value": "ma", "label": "MyAbandonware"}, {"value": "ia", "label": "Internet Archive"}],
                "value": "all",
            },
        ],
    )


def _search(args):
    values = args.get("values") or {}
    context = args.get("context") or {}
    query = (args.get("query") or values.get("query") or "").strip()
    if not query:
        return _ok({"results": []})
    site = values.get("site") or "all"
    system = ((context.get("system") or {}).get("id") or args.get("platform") or "").lower()
    state = _state()
    results = []
    failures = []
    if state["ma"] and site in ("all", "ma"):
        try:
            results.extend(_ma_search(query, system))
        except Challenge:
            failures.append(CHALLENGE_TEXT)
        except HostError as error:
            failures.append(error.message)
    if state["ia"] and site in ("all", "ia"):
        try:
            results.extend(_ia_search(query, state, system))
        except HostError as error:
            failures.append(error.message)
    if not results and failures:
        return _error("FAILED", failures[0])
    return _ok({"results": results})


def _featured(args):
    """MyAbandonware's newest additions (droidtop docs/plugin-api.md 1.8, Droidtop/tracker#612), for Get new games: on
    droidtop's own page all of them, on a system's page the ones for it, and none for a system MyAbandonware has no
    games for."""
    system = str(args.get("platform") or "").strip().lower()
    if not _state()["ma"]:
        return _ok({"results": []})
    if system and not (SYSTEM_PLATFORMS.get(system) or [k for k, v in PLATFORM_SYSTEMS.items() if v == system]):
        return _ok({"results": []})
    try:
        limit = max(1, min(int(args.get("limit") or 20), 60))
    except (TypeError, ValueError):
        limit = 20
    try:
        rows = _ma_recent(system)
    except Challenge:
        return _error("FAILED", CHALLENGE_TEXT)
    return _ok({"results": rows[:limit]})


def _detail(args):
    ref = args.get("ref") or {}
    if ref.get("site") == "ma":
        try:
            game, _headers = _ma_game(ref.get("slug"))
        except Challenge:
            return _error("FAILED", CHALLENGE_TEXT)
        rows = []
        if game["summary"]:
            rows.append(_info("about", "About", None, game["summary"][:480]))
        options = []
        for section in game["sections"]:
            for file in section["files"]:
                parts = [section["name"]] + [p for p in (file["version"], file["language"], file["size"]) if p]
                options.append({"value": file["path"], "label": " · ".join(parts)[:190]})
        if not options:
            rows.append(_info("none", "No download listed", None, "MyAbandonware lists no file for this game."))
            return _ok(_view(game["title"] or ref.get("slug"), rows, "From MyAbandonware"))
        rows.append({"type": "choice", "id": "file", "title": "Download", "options": options[:100], "value": options[0]["value"]})
        rows.append(
            {
                "type": "button",
                "id": "acquire",
                "title": "Download",
                "action": {"kind": "job", "op": "acquire", "title": "Download " + (game["title"] or "game"), "args": {"ref": {"site": "ma", "slug": ref.get("slug")}}},
            }
        )
        return _ok(_view(game["title"] or ref.get("slug"), rows, "From MyAbandonware"))
    if ref.get("site") == "ia":
        try:
            meta, files = _ia_item(ref.get("id"))
        except HostError as error:
            return _error(error.code, error.message)
        rows = []
        for key, label in (("year", "Year"), ("publisher", "Publisher"), ("creator", "By")):
            value = meta.get(key)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            if value:
                rows.append(_info(key, label, str(value)[:120]))
        description = meta.get("description")
        if isinstance(description, list):
            description = " ".join(str(d) for d in description)
        if description:
            rows.append(_info("about", "About", None, _text(str(description))[:480]))
        if not files:
            rows.append(_info("none", "No download listed", None, "This item has no game file the plugin can offer."))
            return _ok(_view(str(meta.get("title") or ref.get("id"))[:120], rows, "From the Internet Archive"))
        options = [{"value": f["name"], "label": (f["name"] + ((" · " + _size_text(f["size"])) if f["size"] else ""))[:190]} for f in files]
        rows.append({"type": "choice", "id": "file", "title": "Download", "options": options[:100], "value": options[0]["value"]})
        rows.append(
            {
                "type": "button",
                "id": "acquire",
                "title": "Download",
                "action": {"kind": "job", "op": "acquire", "title": "Download " + str(meta.get("title") or "item")[:60], "args": {"ref": {"site": "ia", "id": ref.get("id")}}},
            }
        )
        return _ok(_view(str(meta.get("title") or ref.get("id"))[:120], rows, "From the Internet Archive"))
    return _error("NOT_FOUND", "That result is not one this plugin knows")


SETTINGS_TARGET = "settings.page"


def _facet(fid, kind, label, **fields):
    facet = {"id": fid, "kind": kind, "v": 1, "targets": [SETTINGS_TARGET], "label": label}
    facet.update({k: v for k, v in fields.items() if v is not None})
    return facet


def _settings_facets(state):
    """The settings page (droidtop plugin-api 1.10, ui.facets target settings.page)."""
    signed = _signed_in()
    facets = [
        _facet("ma", "toggle", "MyAbandonware", value=state["ma"], call={"op": "setSite", "args": {"site": "ma"}}),
        _facet("ia", "toggle", "Internet Archive", value=state["ia"], call={"op": "setSite", "args": {"site": "ia"}}),
        _facet("collections", "input", "Internet Archive collections", hint="Separate names with commas.", value=", ".join(state["collections"]), call={"op": "setCollections"}),
        _facet("browser", "status", "MyAbandonware browser check", state="ok" if signed else "off", value="Passed" if signed else "Not passed yet"),
        _facet("open", "action", "Open MyAbandonware", sub="Press Done once the page has loaded.", call={"op": "openSite", "kind": "job"}),
    ]
    if signed:
        facets.append(_facet("clear", "action", "Forget the browser session", confirm="confirm", call={"op": "clearSession"}))
    return {"facets": facets}


def _value(args, key):
    """A facet's committed value (`value`, droidtop plugin-api 1.10), or the view's value an older droidtop sends."""
    if "value" in args:
        return args["value"]
    return (args.get("values") or {}).get(key)


def _set_site(args):
    state = _state()
    site = args.get("site")
    if site in ("ma", "ia"):
        value = _value(args, site)
        state[site] = value is True or str(value).lower() == "true"
    else:
        values = args.get("values") or {}
        for key in ("ma", "ia"):
            if key in values:
                state[key] = str(values[key]).lower() == "true"
    _write_json(STATE_FILE, state)
    return _ok({"message": "Saved"})


def _set_collections(args):
    text = str(_value(args, "collections") or "")
    chosen = []
    for part in re.split(r"[,\s]+", text):
        match = re.search(r"archive\.org/details/([^/?#]+)", part)
        part = match.group(1) if match else part
        if IA_ID.match(part) and part not in chosen:
            chosen.append(part)
    state = _state()
    state["collections"] = chosen[:12] or list(DEFAULT_COLLECTIONS)
    _write_json(STATE_FILE, state)
    return _ok({"message": "%d collection(s) saved" % len(state["collections"])})


# ------------------------------------------------------------------------------------------------- the job


def _acquire_ma(args):
    ref = args.get("ref") or {}
    slug = ref.get("slug")
    path = str((args.get("values") or {}).get("file") or "")
    try:
        game, headers = _ma_game(slug)
    except Challenge:
        return {"ok": False, "error": CHALLENGE_TEXT}
    chosen = None
    system = ""
    for section in game["sections"]:
        for file in section["files"]:
            if file["path"] == path or (not path and chosen is None):
                chosen, system = file, system_for_platform(re.sub(r"\s+(Version|ROM)$", "", section["name"]))
    if chosen is None:
        return {"ok": False, "error": "Choose which download first"}
    url = MA + chosen["path"]
    referer = MA + "/game/" + slug
    cookie = re.search(r"PHPSESSID=([0-9A-Za-z]+)", str(headers.get("set-cookie", "")))
    sent = {"Referer": referer}
    if cookie:
        sent["Cookie"] = "PHPSESSID=" + cookie.group(1)
    fallback = (slug or "game") + ".zip"
    try:
        status, final, answer, _body = _fetch(url, method="HEAD", headers=sent, session=False)
    except HostError as error:
        status, final, answer = 0, url, {}
    kind = str(answer.get("content-type", "")).lower()
    if status == 200 and final != url and "html" not in kind:
        disposition = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', str(answer.get("content-disposition", "")))
        name = _file_name(disposition.group(1) if disposition else urllib.parse.urlsplit(final).path, fallback)
        download = _unpacking({"url": url, "headers": sent, "fileName": name})
        length = str(answer.get("content-length", ""))
        if length.isdigit() and int(length) > 0:
            download["size"] = int(length)
        return _done_ma(game, download, system)
    # The site would not hand the file to a plain request (the browser check, or no session): the browser view can.
    if _signed_in():
        try:
            data = _data("web.session", "open_in_session", {"url": url})
        except HostError as error:
            return {"ok": False, "error": error.message}
        if data.get("captured") and isinstance(data.get("download"), dict):
            return _done_ma(game, data["download"], system)
        return {"ok": False, "error": "That page started no download."}
    return {"ok": False, "error": CHALLENGE_TEXT}


def _done_ma(game, download, system):
    if "session" not in download:
        _unpacking(download)
    values = {"message": "Downloading " + (game["title"] or "the game"), "download": json.dumps(download)}
    if system:
        values["system"] = system
    return {"ok": True, "values": values}


def _acquire_ia(args):
    ref = args.get("ref") or {}
    identifier = ref.get("id")
    name = str((args.get("values") or {}).get("file") or "")
    try:
        meta, files = _ia_item(identifier)
    except HostError as error:
        return {"ok": False, "error": error.message}
    chosen = None
    for file in files:
        if file["name"] == name or (not name and chosen is None):
            chosen = file
    if chosen is None:
        return {"ok": False, "error": "Choose which file first"}
    download = _unpacking(
        {
            "url": IA_DOWNLOAD + urllib.parse.quote(identifier) + "/" + urllib.parse.quote(chosen["name"]),
            "fileName": _file_name(chosen["name"], identifier + ".zip"),
        }
    )
    if chosen["size"] > 0:
        download["size"] = chosen["size"]
    collection = meta.get("collection")
    collection = collection if isinstance(collection, list) else [collection]
    values = {"message": "Downloading " + str(meta.get("title") or identifier)[:80], "download": json.dumps(download)}
    if "softwarelibrary_msdos_games" in collection or "softwarelibrary_msdos" in collection:
        values["system"] = "dos"
    elif "softwarelibrary_win3" in collection:
        values["system"] = "windows3x"
    return {"ok": True, "values": values}


def _open_site():
    try:
        done = _data("web.session", "sign_in", {"url": LOGIN_URL}).get("signedIn")
    except HostError as error:
        return {"ok": False, "error": error.message}
    return {"ok": True, "values": {"message": "Browser check passed" if done else "Closed before the page finished"}}


# --------------------------------------------------------------------------------------------- entry points


def handle(call_json):
    try:
        call = json.loads(call_json)
    except ValueError:
        return _error("INVALID_ARGS", "not JSON")
    point = call.get("point")
    op = call.get("op")
    args = call.get("args") or {}
    try:
        if point == "library.sources":
            if op == "form":
                return _ok(_form())
            if op == "search":
                return _search(args)
            if op == "featured":
                return _featured(args)
            if op == "detail":
                return _detail(args)
        elif point == "ui.facets":
            if op == "get":
                return _ok(_settings_facets(_state()) if args.get("target") == SETTINGS_TARGET else {"facets": []})
            if op == "setSite":
                return _set_site(args)
            if op == "setCollections":
                return _set_collections(args)
            if op == "clearSession":
                _data("web.session", "clear", {})
                return _ok({"message": "Browser session forgotten"})
    except HostError as error:
        return _error(error.code if error.code in ERROR_CODES else "FAILED", error.message)
    return _error("UNSUPPORTED", "Unsupported point/op: %s/%s" % (point, op))


_cancelled = set()


def start_job(job_id, call, progress):
    if isinstance(call, str):
        try:
            call = json.loads(call)
        except ValueError:
            call = {}
    if job_id in _cancelled:
        _cancelled.discard(job_id)
        return json.dumps({"ok": False, "error": "cancelled"})
    point, op, args = call.get("point"), call.get("op"), call.get("args") or {}
    try:
        if point == "library.sources" and op == "acquire":
            progress(10, "Asking the site")
            ref = args.get("ref") or {}
            if ref.get("site") == "ma":
                return json.dumps(_acquire_ma(args))
            if ref.get("site") == "ia":
                return json.dumps(_acquire_ia(args))
            return json.dumps({"ok": False, "error": "That result is not one this plugin knows"})
        if point == "ui.facets" and op == "openSite":
            return json.dumps(_open_site())
    except HostError as error:
        return json.dumps({"ok": False, "error": error.message})
    return json.dumps({"ok": False, "error": "not offered: %s %s" % (point, op)})


def cancel_job(job_id):
    _cancelled.add(job_id)


def on_unload():
    pass
