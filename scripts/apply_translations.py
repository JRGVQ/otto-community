#!/usr/bin/env python3
"""Fill msgstr values in a locale catalog from a JSON {msgid: msgstr} mapping.

Usage:
    .venv/bin/python scripts/apply_translations.py <locale> <mapping.json> [--overwrite]

By default only *empty* msgstr entries are filled (safe to re-run; never clobbers
a human review). Pass --overwrite to replace existing translations too. msgids not
present in the catalog are reported and ignored. Reads/writes with Babel's own PO
serializer so catalog metadata and comments survive untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    overwrite = "--overwrite" in sys.argv
    if len(args) != 2:
        print(__doc__)
        return 2
    locale, mapping_path = args
    po_path = ROOT / "app" / "translations" / locale / "LC_MESSAGES" / "messages.po"
    if not po_path.exists():
        print(f"! no catalog for '{locale}' at {po_path}")
        return 1

    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))

    with po_path.open("rb") as fh:
        catalog = read_po(fh)

    filled = skipped = missing = 0
    seen = set()
    for msgid, msgstr in mapping.items():
        msg = catalog.get(msgid)
        if msg is None:
            missing += 1
            continue
        seen.add(msgid)
        if msg.string and not overwrite:
            skipped += 1
            continue
        msg.string = msgstr
        msg.flags.discard("fuzzy")
        filled += 1

    # Report catalog entries the mapping didn't cover (helps spot gaps).
    untranslated = [m.id for m in catalog if m.id and not m.string and m.id not in seen]

    with po_path.open("wb") as fh:
        write_po(fh, catalog, sort_output=False, sort_by_file=False, width=0)

    print(f"[{locale}] filled={filled} skipped(existing)={skipped} not-in-catalog={missing} "
          f"still-untranslated={len(untranslated)}")
    if untranslated:
        for mid in untranslated[:20]:
            print(f"    · missing: {mid!r}")
        if len(untranslated) > 20:
            print(f"    … +{len(untranslated) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
