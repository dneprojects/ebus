"""Generate the shipped baseline strings.json + translations/{en,de}.json from the
bundled device configs, using the shared strings_gen module (the same code the
runtime generator uses). With facts-only configs this yields technical English
names plus our curated German; the full per-language names are generated at the
user's site from the CDN. Run from the integration root:

    python tools/gen_strings.py
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "ebus"
_AIOEBUS_SRC = ROOT.parent / "aioebus" / "src"
if _AIOEBUS_SRC.is_dir():
    sys.path.insert(0, str(_AIOEBUS_SRC))

from aioebus.convert import from_dicts  # noqa: E402

# load the package's strings_gen by path (do NOT add PKG to sys.path: it would
# shadow stdlib modules like `select` with the integration's platform files)
_spec = importlib.util.spec_from_file_location("ebus_strings_gen", PKG / "strings_gen.py")
sg = importlib.util.module_from_spec(_spec)
sys.modules["ebus_strings_gen"] = sg
_spec.loader.exec_module(sg)


def build():
    msgs = []
    for jf in sorted((PKG / "configs" / "vaillant").glob("*.json")):
        if jf.name == "index.json":
            continue
        msgs += from_dicts(json.loads(jf.read_text(encoding="utf-8")))

    en = sg.build_strings(msgs, "en")
    de = sg.build_strings(msgs, "de")
    out = {"strings.json": en, "translations/en.json": en, "translations/de.json": de}
    for rel, data in out.items():
        (PKG / rel).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    e, d = en["entity"], de["entity"]
    print(f"sensors={len(e['sensor'])} numbers={len(e['number'])} selects={len(e['select'])}; "
          f"de entries={sum(len(v) for v in d.values())}")


if __name__ == "__main__":
    build()
