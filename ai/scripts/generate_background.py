from __future__ import annotations

from pathlib import Path

from prompt_builder import build_image_prompt


def write_prompt_file(
    output_path: Path,
    dominant_color: str,
    category: str,
    season: str,
    platform: str,
) -> dict:
    prompt_payload = build_image_prompt(
        dominant_color=dominant_color,
        category=category,
        season=season,
        platform=platform,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_payload["prompt"], encoding="utf-8")
    return prompt_payload


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Build an SDXL background prompt from product conditions."
    )
    parser.add_argument("--color", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--platform", default="instagram")
    parser.add_argument("--output", type=Path, default=Path("ai/outputs/logs/latest_prompt.txt"))
    args = parser.parse_args()

    payload = write_prompt_file(
        output_path=args.output,
        dominant_color=args.color,
        category=args.category,
        season=args.season,
        platform=args.platform,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))

