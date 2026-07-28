from __future__ import annotations

from pathlib import Path

from compose_poster import compose_poster
from prompt_builder import build_image_prompt
from remove_background import remove_background


ROOT = Path(__file__).resolve().parents[2]


def run_local_composition_test(
    product_id: str,
    brand: str,
    product_name: str,
    price: str,
    dominant_color: str,
    category: str,
    season: str,
    platform: str,
) -> Path:
    raw_photo = ROOT / "data" / "raw" / "product_photos" / f"{product_id}.jpg"
    cutout = ROOT / "data" / "processed" / "cutouts" / f"{product_id}.png"
    background = ROOT / "ai" / "outputs" / "backgrounds" / f"{product_id}_{season}.png"
    poster = ROOT / "data" / "processed" / "posters" / f"{product_id}_{platform}.jpg"

    prompt_payload = build_image_prompt(dominant_color, category, season, platform)

    if not raw_photo.exists():
        raise FileNotFoundError(f"Missing product photo: {raw_photo}")
    if not background.exists():
        raise FileNotFoundError(
            f"Missing generated background: {background}. Generate it in Colab first using this prompt:\n"
            f"{prompt_payload['prompt']}"
        )

    remove_background(raw_photo, cutout)
    compose_poster(
        background_path=background,
        product_cutout_path=cutout,
        output_path=poster,
        brand=brand,
        product_name=product_name,
        price=price,
        cta="Disponible maintenant",
        platform=platform,
        text_color=prompt_payload["overlay_text_color"],
        cta_color=prompt_payload["cta_color"],
    )
    return poster


if __name__ == "__main__":
    output = run_local_composition_test(
        product_id="p001",
        brand="Example Brand",
        product_name="Hydra Glow Serum",
        price="129 DH",
        dominant_color="pink",
        category="serum",
        season="summer",
        platform="instagram",
    )
    print(f"Poster generated: {output}")

