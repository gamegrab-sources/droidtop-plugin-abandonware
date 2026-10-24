# droidtop-plugin-abandonware

An **unofficial** droidtop plugin from the [gamegrab-sources](https://github.com/gamegrab-sources) organisation. It is
not part of droidtop and not affiliated with MyAbandonware or the Internet Archive. Whether a game may be downloaded
where you live is your call.

It searches [MyAbandonware](https://www.myabandonware.com) and the Internet Archive's software library through droidtop's
Get games screens (`library.sources`: search, detail, acquire), and droidtop's downloader fetches the file you pick.

## MyAbandonware

- Get new games (`featured`): the newest additions from `/browse/recent/`, for the system droidtop is showing.
- Search `/search/q/<words>`, game pages with a download per platform and version, and the download address
  `/download/<id>` (it redirects to a short-lived file address; the plugin sends the session cookie the game page set and
  the game page as referer, and asks once with HEAD for the file's name and size).
- **The browser check.** The site puts its search behind a JavaScript and captcha check ("Cake Test"). This plugin does
  not try to pass it, and says so in plain words when a request meets it. Settings > "Open MyAbandonware" opens the site
  in droidtop's own browser view (`web.session`), where you pass it as in any browser; the plugin then asks through that
  session, and uses the same view for a download a plain request cannot get (`open_in_session`).
- The site's robots.txt disallows `/search/*` and `/download/*` for crawlers. The plugin never crawls: it makes the request
  a person's own search or download press makes, one at a time.

## Internet Archive

advancedsearch over `softwarelibrary_msdos_games` and `softwarelibrary_win3` (change them in Settings), an item's
files from `metadata/<item>/metadata` and `/files`, download from `archive.org/download/<item>/<file>`.

## What it tells droidtop with a download

`download` as the acquire reply defines it, with `unpack: "archive"` for a zip, 7z or rar, and `system`: droidtop's id for
the game's system (`dos`, `windows`, `macintosh`, `amiga`, ...), by which droidtop places the game (Droidtop/tracker#417). The
engine hint (`engine`) only names Enginehost engines, so this plugin does not set it.

## Layout

- `src/plugin.py`: the whole plugin; standard library only; contained (no sockets).
- `fixtures/`: trimmed copies of MyAbandonware pages the parser is tested on.
- `build.sh`, `sign.sh`, `.github/workflows/build.yml`: as in droidtop-plugin-bios; CI signs with this repository's key from the
  gamegrab-sources master and publishes a release per working build of main.
- `python3 test_plugin.py` runs the tests.

Licence: GPL-3.0.

## Signing

The bundle carries `manifest.jws`: a detached ES256 JWS over the exact bytes of `manifest.json`, with this repository's leaf certificate in `x5c` (the root itself is not included). The leaf comes from the repository secrets `PLUGIN_SIGNING_KEY` and `PLUGIN_SIGNING_CERT` and was issued by the gamegrab root, a third-party anchor that people choose to trust (it is not pinned by the droidtop app); it names the plugin's origin and id. `sign_jws.py` writes the signature and checks it against the leaf before packing. `manifest.sig` stays in the bundle for older droidtop builds. Expiry dates are not checked.

`droidtop-plugin-key.json` in the repository root carries the gamegrab root certificate; droidtop reads it to anchor the `gamegrab` origin at that root.
