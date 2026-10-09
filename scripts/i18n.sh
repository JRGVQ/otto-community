#!/usr/bin/env bash
# i18n workflow helper for Otto (Flask-Babel / gettext).
#
#   scripts/i18n.sh extract     # rebuild translations/messages.pot from the source
#   scripts/i18n.sh init <loc>   # create a brand-new catalog for a locale
#   scripts/i18n.sh update       # merge new/changed strings into every locale's .po
#   scripts/i18n.sh compile      # compile every .po into the .mo the app loads
#
# Typical loop after adding/altering translatable strings:
#   scripts/i18n.sh extract && scripts/i18n.sh update   # then translate the .po files
#   scripts/i18n.sh compile
set -euo pipefail

cd "$(dirname "$0")/.."
PYBABEL="${PYBABEL:-.venv/bin/pybabel}"
LOCALES=(nl fr en)
# Catalogs live inside the app package — Flask-Babel resolves
# BABEL_TRANSLATION_DIRECTORIES relative to app.root_path (= app/).
TRANS=app/translations
POT=$TRANS/messages.pot

cmd="${1:-}"
case "$cmd" in
  extract)
    mkdir -p "$TRANS"
    "$PYBABEL" extract -F babel.cfg -k _l -k _n:1,2 -o "$POT" \
      --project=Otto --copyright-holder="Digital Genics" .
    echo "→ wrote $POT"
    ;;
  init)
    loc="${2:?usage: i18n.sh init <locale>}"
    "$PYBABEL" init -i "$POT" -d "$TRANS" -l "$loc"
    ;;
  update)
    "$PYBABEL" update -i "$POT" -d "$TRANS" --no-fuzzy-matching
    ;;
  compile)
    "$PYBABEL" compile -d "$TRANS" --statistics
    ;;
  bootstrap)
    # First-time setup: extract, then init every locale that doesn't exist yet.
    "$0" extract
    for loc in "${LOCALES[@]}"; do
      if [[ ! -f "$TRANS/$loc/LC_MESSAGES/messages.po" ]]; then
        "$PYBABEL" init -i "$POT" -d "$TRANS" -l "$loc"
      fi
    done
    ;;
  *)
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
