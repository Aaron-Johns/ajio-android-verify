"""Write config/ajio_brand_names_deduped.json: the brand list minus the second name of each
colliding pair in inputs/brand_aliases.draft.json (CLAUDE.md 7.1). inputs/ is never modified.

Run: python -m qa.dedupe_brands
"""
from __future__ import annotations

import json

from qa.brand_resolver import DEDUPED_BRAND_FILE, ORIGINAL_BRAND_FILE, REPO_ROOT, load_brand_list

ALIASES_FILE = REPO_ROOT / "inputs" / "brand_aliases.draft.json"


def dedupe(brands: list[str], collisions: list[list[str]]) -> tuple[list[str], list[str]]:
    drop = {pair[i] for pair in collisions for i in range(1, len(pair))}
    return [b for b in brands if b not in drop], sorted(drop & set(brands))


def main() -> None:
    collisions = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))["_normalized_exact_collisions"]
    brands = load_brand_list(ORIGINAL_BRAND_FILE)
    kept, removed = dedupe(brands, collisions)
    DEDUPED_BRAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEDUPED_BRAND_FILE.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(brands)} -> {len(kept)} brands; removed: {removed}")


if __name__ == "__main__":
    main()
