# Changelog

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
