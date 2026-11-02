"""Generate the shipped baseline strings.json + translations/{en,de}.json from the
bundled device configs, using the shared strings_gen module (the same code the
runtime generator uses). With facts-only configs this yields technical English
names plus our curated German; the full per-language names are generated at the
user's site from the CDN. Run from the integration root:

    python tools/gen_strings.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pure_loader import PKG, pure

FILES = ("strings.json", "translations/en.json", "translations/de.json")


def build() -> dict[str, dict]:
    """The baseline files by path (relative to the package), from the bundled defs."""
    definitions, sg = pure("definitions"), pure("strings_gen")
    msgs = []
    for jf in sorted(definitions.CONFIG_DIR.glob("*.json")):
        if jf.name != "index.json":
            msgs += definitions.load_file(jf.name)
    en, de = sg.build_strings(msgs, "en"), sg.build_strings(msgs, "de")
    return dict(zip(FILES, (en, en, de), strict=True))


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    files = build()
    for rel, data in files.items():
        (PKG / rel).write_text(render(data), encoding="utf-8")
    e, d = files["translations/en.json"]["entity"], files["translations/de.json"]["entity"]
    print(f"sensors={len(e['sensor'])} numbers={len(e['number'])} selects={len(e['select'])}; "
          f"de entries={sum(len(v) for v in d.values())}")


if __name__ == "__main__":
    main()
