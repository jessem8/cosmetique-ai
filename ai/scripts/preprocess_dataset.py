from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}


def _iter_images(source_dir: Path) -> list[Path]:
    return sorted(
        path for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _perceptual_hash(image: Image.Image) -> str | None:
    try:
        import imagehash
    except ImportError:
        return None
    return str(imagehash.phash(image))


def _resize_keep_ratio(image: Image.Image, max_side: int) -> Image.Image:
    width, height = image.size
    if max(width, height) <= max_side:
        return image
    ratio = max_side / max(width, height)
    size = (round(width * ratio), round(height * ratio))
    return image.resize(size, Image.LANCZOS)


def preprocess_dataset(source_dir: Path, output_dir: Path, max_side: int = 1280) -> dict:
    output_images_dir = output_dir / "images"
    output_images_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "max_side": max_side,
        "items": [],
        "errors": [],
        "exact_duplicates": {},
        "perceptual_duplicates": {},
    }

    exact_seen: dict[str, list[str]] = {}
    phash_seen: dict[str, list[str]] = {}

    for index, input_path in enumerate(_iter_images(source_dir), start=1):
        try:
            exact_hash = _file_sha256(input_path)
            exact_seen.setdefault(exact_hash, []).append(input_path.name)

            with Image.open(input_path) as raw_image:
                image = ImageOps.exif_transpose(raw_image).convert("RGB")
                original_size = image.size
                image = _resize_keep_ratio(image, max_side)
                phash = _perceptual_hash(image)
                if phash:
                    phash_seen.setdefault(phash, []).append(input_path.name)

                output_name = f"product_{index:04d}.jpg"
                output_path = output_images_dir / output_name
                image.save(output_path, format="JPEG", quality=94, optimize=True)

            report["items"].append({
                "source": input_path.name,
                "output": output_name,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "processed_width": image.size[0],
                "processed_height": image.size[1],
                "sha256": exact_hash,
                "phash": phash,
            })
        except Exception as exc:
            report["errors"].append({"file": input_path.name, "error": str(exc)})

    report["exact_duplicates"] = {
        key: values for key, values in exact_seen.items() if len(values) > 1
    }
    report["perceptual_duplicates"] = {
        key: values for key, values in phash_seen.items() if len(values) > 1
    }

    report_path = output_dir / "preprocessing_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare cosmetic product photos for AI pipeline testing.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/raw/product_photos"),
        help="Raw product image directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/product_photos"),
        help="Processed dataset output directory.",
    )
    parser.add_argument("--max-side", type=int, default=1280)
    args = parser.parse_args()

    report = preprocess_dataset(args.source, args.output, args.max_side)
    print("DATASET PREPROCESSING REPORT")
    print(f"Images processed: {len(report['items'])}")
    print(f"Errors: {len(report['errors'])}")
    print(f"Exact duplicate groups: {len(report['exact_duplicates'])}")
    print(f"Perceptual duplicate groups: {len(report['perceptual_duplicates'])}")
    print(f"Report: {args.output / 'preprocessing_report.json'}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
