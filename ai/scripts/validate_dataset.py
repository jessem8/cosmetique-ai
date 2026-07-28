from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTS_PATH = ROOT / "data" / "metadata" / "products.csv"
PHOTOS_DIR = ROOT / "data" / "raw" / "product_photos"
RULES_PATH = ROOT / "ai" / "prompts" / "visual_rules.json"
CAPTION_PROMPTS_PATH = ROOT / "ai" / "prompts" / "caption_prompts.json"

REQUIRED_COLUMNS = {
    "id",
    "image_file",
    "brand",
    "name",
    "category",
    "dominant_color",
    "season",
    "tone",
    "price",
    "cta",
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def canonical(value: str) -> str:
    return (value or "").strip().lower().replace("_", " ")


def alias_lookup(value: str, aliases: dict[str, str]) -> str | None:
    normalized_aliases = {canonical(key): target for key, target in aliases.items()}
    return normalized_aliases.get(canonical(value))


def image_exists(image_file: str) -> tuple[bool, str]:
    direct_path = PHOTOS_DIR / image_file
    if direct_path.exists():
        return True, image_file

    stem = Path(image_file).stem
    for extension in [".jpg", ".jpeg", ".png", ".webp"]:
        candidate = PHOTOS_DIR / f"{stem}{extension}"
        if candidate.exists():
            return True, candidate.name

    return False, image_file


def validate_dataset() -> int:
    rules = load_json(RULES_PATH)
    captions = load_json(CAPTION_PROMPTS_PATH)
    aliases = rules.get("aliases", {})
    accepted_tones = {canonical(key) for key in captions.get("tones", {}).keys()}

    errors: list[str] = []
    warnings: list[str] = []

    if not PRODUCTS_PATH.exists():
        errors.append(f"Missing CSV: {PRODUCTS_PATH}")
        print_report(errors, warnings, 0)
        return 1

    with PRODUCTS_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        headers = set(reader.fieldnames or [])
        missing_columns = sorted(REQUIRED_COLUMNS - headers)
        if missing_columns:
            errors.append(f"Missing columns: {', '.join(missing_columns)}")

        rows = list(reader)

    seen_ids: set[str] = set()
    seen_images: set[str] = set()

    for index, row in enumerate(rows, start=2):
        product_id = (row.get("id") or "").strip()
        image_file = (row.get("image_file") or "").strip()

        if not product_id:
            errors.append(f"Line {index}: missing id")
        elif product_id in seen_ids:
            errors.append(f"Line {index}: duplicate id '{product_id}'")
        else:
            seen_ids.add(product_id)

        if not image_file:
            errors.append(f"Line {index}: missing image_file")
        else:
            exists, matched_name = image_exists(image_file)
            if not exists:
                errors.append(f"Line {index} ({product_id}): image not found '{image_file}'")
            else:
                seen_images.add(matched_name)
                if matched_name != image_file:
                    warnings.append(
                        f"Line {index} ({product_id}): CSV says '{image_file}', actual file is '{matched_name}'. This is accepted."
                    )

        color = alias_lookup(row.get("dominant_color", ""), aliases.get("product_colors", {}))
        if color is None:
            errors.append(
                f"Line {index} ({product_id}): unsupported dominant_color '{row.get('dominant_color', '')}'"
            )

        category = alias_lookup(row.get("category", ""), aliases.get("product_types", {}))
        if category is None:
            errors.append(
                f"Line {index} ({product_id}): unsupported category '{row.get('category', '')}'"
            )

        season = alias_lookup(row.get("season", ""), aliases.get("seasons", {}))
        if season is None:
            errors.append(f"Line {index} ({product_id}): unsupported season '{row.get('season', '')}'")

        tone = canonical(row.get("tone", ""))
        if tone and tone not in accepted_tones:
            warnings.append(
                f"Line {index} ({product_id}): tone '{row.get('tone', '')}' has no custom copy style yet. It will use a generic style later."
            )

    image_files = {
        path.name
        for path in PHOTOS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    }
    unused_images = sorted(image_files - seen_images)
    for image_name in unused_images:
        warnings.append(f"Unused image file: {image_name}")

    print_report(errors, warnings, len(rows))
    return 1 if errors else 0


def print_report(errors: list[str], warnings: list[str], row_count: int) -> None:
    print("DATASET VALIDATION REPORT")
    print(f"Rows checked: {row_count}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")

    if errors:
        print("\nERRORS")
        for error in errors:
            print(f"- {error}")

    if warnings:
        print("\nWARNINGS")
        for warning in warnings:
            print(f"- {warning}")

    if not errors:
        print("\nStatus: OK. Dataset can move to background removal tests.")


if __name__ == "__main__":
    raise SystemExit(validate_dataset())

