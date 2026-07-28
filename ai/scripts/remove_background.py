from __future__ import annotations

from pathlib import Path


def remove_background(input_path: Path, output_path: Path) -> None:
    try:
        from rembg import remove
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency. Install with: pip install rembg pillow"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(input_path).convert("RGBA")
    result = remove(image)
    result.save(output_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Remove product photo background.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    remove_background(args.input, args.output)

