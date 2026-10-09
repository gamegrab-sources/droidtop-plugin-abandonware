#!/usr/bin/env bash
# Builds the plugin bundle (unsigned): build/plugin.py (src/plugin.py as it is) and build/manifest.json (the template
# with the payload hash and, in CI, the run number). A python-kind plugin loads one file, plugin.py. Never touches a
# key: sign.sh does.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf -- build
mkdir -p build
cp src/plugin.py build/plugin.py
python3 - <<'PY'
import hashlib, json, os

manifest = json.load(open("manifest.template.json"))
manifest["payload"] = [{"path": "plugin.py", "sha256": hashlib.sha256(open("build/plugin.py", "rb").read()).hexdigest()}]
# The published version is <declared>-<CI run> (Droidtop/tracker#126).
build = os.environ.get("DROIDTOP_PLUGIN_BUILD", "").strip()
if build:
    if not build.isdigit():
        raise SystemExit("DROIDTOP_PLUGIN_BUILD must be a CI run number, got %r" % build)
    manifest["version"] = "%s-%s" % (manifest["version"], build)
json.dump(manifest, open("build/manifest.json", "w"), indent=2, sort_keys=True)
print("payload plugin.py, id=%s, version=%s" % (manifest["id"], manifest["version"]))
PY
python3 -c "import ast; ast.parse(open('build/plugin.py', encoding='utf-8').read())"
echo "Built build/plugin.py and build/manifest.json (unsigned)"
