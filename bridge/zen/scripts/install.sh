#!/usr/bin/env bash
set -euo pipefail

MOD_ID="qwqc-hey-tabby-bridge"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${ZEN_PROFILE_ROOT:-$HOME/.var/app/app.zen_browser.zen/.zen}"
PROFILES_INI="$PROFILE_ROOT/profiles.ini"

if [[ -n "${ZEN_PROFILE:-}" ]]; then
  PROFILE="$ZEN_PROFILE"
else
  PROFILE_REL="$(
    python3 - "$PROFILES_INI" <<'PY'
from configparser import ConfigParser
from pathlib import Path
import sys

ini = Path(sys.argv[1])
cfg = ConfigParser()
cfg.read(ini)

for section in cfg.sections():
    if section.startswith("Install") and cfg.has_option(section, "Default"):
        print(cfg.get(section, "Default"))
        raise SystemExit
for section in cfg.sections():
    if section.startswith("Profile") and cfg.get(section, "Default", fallback="0") == "1":
        print(cfg.get(section, "Path"))
        raise SystemExit
for section in cfg.sections():
    if section.startswith("Profile") and cfg.has_option(section, "Path"):
        print(cfg.get(section, "Path"))
        raise SystemExit
raise SystemExit("No Zen profile found")
PY
  )"
  PROFILE="$PROFILE_ROOT/$PROFILE_REL"
fi

SINE_ROOT="$PROFILE/chrome/sine-mods"
ACTOR_ROOT="$PROFILE/chrome/JS/actors"
ENGINE_ROOT="$PROFILE/chrome/JS"
if [[ ! -d "$PROFILE/chrome/JS" ]]; then
  echo "Sine does not appear to be installed in: $PROFILE" >&2
  exit 1
fi

DEST="$SINE_ROOT/$MOD_ID"
mkdir -p "$DEST" "$ACTOR_ROOT"
cp "$ROOT/theme.json" "$DEST/theme.json"
cp "$ROOT/README.md" "$DEST/README.md"
cp "$ROOT/hey-tabby.uc.js" "$DEST/hey-tabby.uc.js"
cp "$ROOT/actors/QwqcHeyTabbyChild.sys.mjs" "$ACTOR_ROOT/QwqcHeyTabbyChild.sys.mjs"
cp "$ROOT/engine/tabby-engine.xhtml" "$ENGINE_ROOT/tabby-engine.xhtml"

python3 - "$SINE_ROOT/mods.json" "$DEST/theme.json" <<'PY'
import json
from pathlib import Path
import sys

mods_path = Path(sys.argv[1])
theme_path = Path(sys.argv[2])
mods = json.loads(mods_path.read_text()) if mods_path.exists() else {}
theme = json.loads(theme_path.read_text())
old = mods.get(theme["id"], {})
theme["enabled"] = old.get("enabled", True)
theme["no-updates"] = True
theme["origin"] = "local"
mods[theme["id"]] = theme
mods_path.write_text(json.dumps(mods, indent=2) + "\n")
PY

echo "Deployed bundled Tabby Zen bridge $MOD_ID to $DEST"
echo "Actor: $ACTOR_ROOT/QwqcHeyTabbyChild.sys.mjs"
echo "Engine: $ENGINE_ROOT/tabby-engine.xhtml"
echo "Controller: Sine host window (engine has no duplicate controller)"
echo "Restart Zen or toggle the mod in Sine to load it."
