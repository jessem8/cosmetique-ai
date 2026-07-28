from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANVAS_SIZES = {
    "instagram": (1080, 1080),
    "facebook": (1080, 1350),
    "linkedin": (1200, 628),
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def fit_product(product: Image.Image, max_width: int, max_height: int) -> Image.Image:
    product = product.convert("RGBA")
    scale = min(max_width / product.width, max_height / product.height)
    new_size = (int(product.width * scale), int(product.height * scale))
    return product.resize(new_size, Image.LANCZOS)


def add_shadow(canvas: Image.Image, product: Image.Image, position: tuple[int, int]) -> None:
    alpha = product.getchannel("A")
    shadow = Image.new("RGBA", product.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(18)))
    shadow = Image.eval(shadow, lambda px: int(px * 0.35))
    canvas.alpha_composite(shadow, (position[0] + 18, position[1] + 24))


def compose_poster(
    background_path: Path,
    product_cutout_path: Path,
    output_path: Path,
    brand: str,
    product_name: str,
    price: str,
    cta: str,
    platform: str = "instagram",
    text_color: str = "#2D2A26",
    cta_color: str = "#D8B56D",
) -> None:
    width, height = CANVAS_SIZES[platform]
    background = Image.open(background_path).convert("RGB").resize((width, height), Image.LANCZOS)
    canvas = background.convert("RGBA")

    product = Image.open(product_cutout_path).convert("RGBA")
    if platform == "linkedin":
        product = fit_product(product, int(width * 0.36), int(height * 0.76))
        product_x = int(width * 0.18 - product.width / 2)
        product_y = int(height * 0.50 - product.height / 2)
        text_x = int(width * 0.52)
        text_y = int(height * 0.22)
    else:
        product = fit_product(product, int(width * 0.48), int(height * 0.58))
        product_x = int(width * 0.50 - product.width / 2)
        product_y = int(height * 0.43 - product.height / 2)
        text_x = 90
        text_y = int(height * 0.76)

    add_shadow(canvas, product, (product_x, product_y))
    canvas.alpha_composite(product, (product_x, product_y))

    draw = ImageDraw.Draw(canvas)
    brand_font = load_font(34, bold=True)
    name_font = load_font(52 if platform != "linkedin" else 44, bold=True)
    price_font = load_font(42 if platform != "linkedin" else 34, bold=True)
    cta_font = load_font(30, bold=True)

    draw.text((text_x, text_y), brand.upper(), font=brand_font, fill=text_color)
    draw.text((text_x, text_y + 48), product_name, font=name_font, fill=text_color)
    draw.text((text_x, text_y + 122), price, font=price_font, fill=text_color)

    cta_box = (text_x, text_y + 185, text_x + 360, text_y + 245)
    draw.rounded_rectangle(cta_box, radius=18, fill=cta_color)
    draw.text((text_x + 34, text_y + 200), cta, font=cta_font, fill="#FFFFFF")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, quality=95)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compose a premium cosmetic poster.")
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--cta", default="Disponible maintenant")
    parser.add_argument("--platform", choices=CANVAS_SIZES.keys(), default="instagram")
    parser.add_argument("--text-color", default="#2D2A26")
    parser.add_argument("--cta-color", default="#D8B56D")
    args = parser.parse_args()

    compose_poster(
        background_path=args.background,
        product_cutout_path=args.product,
        output_path=args.output,
        brand=args.brand,
        product_name=args.name,
        price=args.price,
        cta=args.cta,
        platform=args.platform,
        text_color=args.text_color,
        cta_color=args.cta_color,
    )

