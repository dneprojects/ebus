"""Attach upstream display translations (``tr``) to the bundled device configs
from the ebus.github.io CDN (``/en/`` + ``/de/`` CSVs), matched by
(direction, pbsb, id, name). Structure and selection are untouched -- only the
per-language display text is added.

    python tools/add_cdn_tr.py

LICENSE -- READ THIS: the CDN text (comments and translations) is GPL-3.0. This
writes that English/German text into the bundled JSON, which is fine for a
PRIVATE/LOCAL installation. The enriched configs (and the comments already in
them) MUST NOT be distributed publicly -- HACS or Home Assistant Core -- without
john30's permissive relicensing of ebusd-configuration. Before going public,
strip `tr` and `comment` (facts-only), or obtain that grant.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "ebus"
_AIOEBUS_SRC = ROOT.parent / "aioebus" / "src"
if _AIOEBUS_SRC.is_dir():
    sys.path.insert(0, str(_AIOEBUS_SRC))

from aioebus.convert import attach_translations, from_dicts, to_dicts  # noqa: E402
from aioebus.messages import parse_csv  # noqa: E402

CDN = "https://ebus.github.io"
LANGS = ("en", "de")
# bundled config file -> upstream CDN source path(s), without the lang/.csv parts.
# eloblock.json and netx3.json are our own definitions with no upstream source.
SOURCES = {
    "ctlv3.json": ["vaillant/15.ctlv3"],
    "hmu.json": ["vaillant/08.hmu", "vaillant/08.hmu.HW5103"],
    "pmw.json": ["vaillant/0a.pmw.hwc"],
    "vr_71.json": ["vaillant/26.vr_71"],
    "v32.hp.json": ["vaillant/08.v3x"],
    "broadcast.json": ["broadcast", "vaillant/broadcast"],
}


def _fetch(lang: str, path: str) -> str:
    with urllib.request.urlopen(f"{CDN}/{lang}/{path}.csv", timeout=15) as r:
        return r.read().decode("utf-8", "replace")


def main() -> None:
    cfg = PKG / "configs" / "vaillant"
    for fname, paths in SOURCES.items():
        jf = cfg / fname
        if not jf.exists():
            print(f"skip {fname}: not found")
            continue
        defs = from_dicts(json.loads(jf.read_text(encoding="utf-8")))
        by_lang = {}
        for lang in LANGS:
            msgs = []
            for p in paths:
                try:
                    msgs += parse_csv(_fetch(lang, p))
                except Exception as err:  # noqa: BLE001 - report and carry on
                    print(f"  {lang}/{p}: {err}")
            if msgs:
                by_lang[lang] = msgs
        if not by_lang:
            print(f"{fname}: no CDN source reached")
            continue
        attach_translations(defs, by_lang)
        n = sum(1 for m in defs if m.tr)
        jf.write_text(
            json.dumps(to_dicts(defs), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{fname}: tr on {n}/{len(defs)} messages (langs={','.join(by_lang)})")


if __name__ == "__main__":
    main()
