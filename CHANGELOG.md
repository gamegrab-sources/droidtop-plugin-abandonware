# Changelog

## 0.4.1

- The release of 0.4.0's facets and contract 3 changes. 0.4.0's two commits shared one declared version, so CI's version rule refused the second
  and 0.4.0 was never released; this version carries both unchanged.

## 0.4.0

- The settings are `settings.page` facets (droidtop docs/plugin-api.md 1.10) in place of the `ui.settings` view: the
  two site switches, the Internet Archive collections, the browser check's state, Open MyAbandonware and, once
  passed, Forget the browser session. Each switch and the collections are saved as they are committed, from
  droidtop's `value`.
- The manifest says `"contractVersion": 3`: it needs facets, so a droidtop built before them refuses this version
  and keeps the one installed, rather than install a plugin whose settings it cannot draw (droidtop docs/plugin-api.md
  7, "Contract 3").

## 0.3.2

- Names its kind for droidtop's Plugins page (`category`, docs/plugin-api.md 1.2, Droidtop/tracker#179).

## 0.3.1

- Declares the systems it serves (DOS, Windows 3.x and Windows), so droidtop lists it under those and in PC searches, not under every console.
  Needs a droidtop that reads `systems` on `library.sources` (docs/plugin-api.md 1.6); an older
  droidtop ignores the field and lists the source everywhere, as before.

## 0.3.0

- Failures in plain words ("MyAbandonware is not answering right now")
  instead of "answered 503" or "answered with something unexpected".
- Shorter text: one-line description and browser-check sentence, no About
  row in Settings (the catalog already marks the plugin Unofficial).
- droidtop's contract 2.1 host names (web.session.get_status, net.request;
  needs droidtop 0.2.0-dev.1767 or newer) and 48 KiB data reads.

## 0.2.0

- Zip, 7z and rar downloads ask droidtop to unpack them (`unpack: archive`) into a folder of their own, and the `system` value is now what droidtop places the game by.
- File names are kept as the source gives them (spaces and accents included).

## 0.1.0

- First version: search and download from MyAbandonware (through droidtop's browser view when the site's browser check
  asks for it) and the Internet Archive's MS-DOS and Windows 3.x software library; a settings page for the sites and
  collections.
