from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "ai" / "prompts" / "visual_rules.json"
IMAGE_PROMPTS_PATH = ROOT / "ai" / "prompts" / "image_prompts.json"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_value(value: str, aliases: dict[str, str], fallback: str) -> str:
    normalized = (value or "").strip().lower().replace("_", " ")
    compact_aliases = {
        key.strip().lower().replace("_", " "): target for key, target in aliases.items()
    }
    return compact_aliases.get(normalized, fallback)


def build_image_prompt(
    dominant_color: str,
    category: str,
    season: str,
    platform: str,
) -> dict:
    rules = load_json(RULES_PATH)
    image_prompts = load_json(IMAGE_PROMPTS_PATH)

    aliases = rules.get("aliases", {})
    normalized_color = normalize_value(
        dominant_color, aliases.get("product_colors", {}), "white"
    )
    normalized_category = normalize_value(
        category, aliases.get("product_types", {}), "parapharmacy"
    )
    normalized_season = normalize_value(
        season, aliases.get("seasons", {}), "neutral_luxury"
    )

    color_rule = rules["product_colors"].get(normalized_color, rules["product_colors"]["white"])
    type_rule = rules["product_types"].get(normalized_category, rules["product_types"]["parapharmacy"])
    season_rule = rules["seasons"].get(normalized_season, rules["seasons"]["neutral_luxury"])
    platform_rule = image_prompts["platforms"].get(platform, image_prompts["platforms"]["instagram"])

    prompt_parts = [
        image_prompts["base_prompt"],
        color_rule["background_prompt"],
        type_rule["prompt_addon"],
        season_rule["prompt_addon"],
        platform_rule["composition_hint"],
    ]

    return {
        "prompt": ", ".join(prompt_parts),
        "negative_prompt": rules["global_negative_prompt"],
        "generation_width": platform_rule["generation_width"],
        "generation_height": platform_rule["generation_height"],
        "final_width": platform_rule["width"],
        "final_height": platform_rule["height"],
        "steps": image_prompts["model"]["steps"],
        "guidance_scale": image_prompts["model"]["guidance_scale"],
        "seed": image_prompts["model"]["seed"],
        "palette": color_rule["palette"],
        "overlay_text_color": color_rule["overlay_text_color"],
        "cta_color": color_rule["cta_color"],
        "product_scale": type_rule["product_scale"],
        "normalized_color": normalized_color,
        "normalized_category": normalized_category,
        "normalized_season": normalized_season,
    }


if __name__ == "__main__":
    result = build_image_prompt(
        dominant_color="pink",
        category="serum",
        season="summer",
        platform="instagram",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
