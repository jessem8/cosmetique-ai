# %% [markdown]
# # Service IA Colab corrige - Affiches publicitaires cosmetiques
#
# Objectif:
# - upload d'une photo produit brute
# - detourage
# - generation d'un decor publicitaire avec SDXL Inpainting
# - generation d'un texte marketing, Ollama si disponible, fallback sinon
# - export Instagram, Facebook, LinkedIn
# - endpoint FastAPI qui retourne un ZIP integrable dans ton backend
#
# Important:
# Execute ce notebook dans un runtime Colab frais avec GPU T4.
# Ne melange pas cette installation avec d'anciennes cellules.

# %% [markdown]
# ## Cellule 1 - Installation stable
# Execute cette cellule une seule fois, puis execute la cellule 2 pour redemarrer le runtime.

# %%
!apt-get update -qq
!apt-get install -y zstd

!pip uninstall -y numpy scipy diffusers transformers accelerate peft safetensors rembg onnxruntime onnxruntime-gpu

!pip install -q --no-cache-dir numpy==2.3.5 scipy==1.16.3
!pip install -q --no-cache-dir diffusers==0.35.2 transformers==4.56.2 accelerate==1.10.1 peft==0.17.1 safetensors==0.6.2
!pip install -q --no-cache-dir rembg==2.0.77 onnxruntime-gpu pillow opencv-python-headless requests
!pip install -q --no-cache-dir fastapi uvicorn pyngrok python-multipart nest-asyncio

print("Installation terminee. Execute maintenant la cellule de redemarrage.")

# %% [markdown]
# ## Cellule 2 - Redemarrage obligatoire
# Sans redemarrage, Colab garde souvent les anciennes versions NumPy/SciPy en memoire.

# %%
import os
import signal

os.kill(os.getpid(), signal.SIGKILL)

# %% [markdown]
# ## Cellule 3 - Verification environnement

# %%
import numpy
import scipy
import torch

print("numpy:", numpy.__version__)
print("scipy:", scipy.__version__)
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available())
print("gpu:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")

from diffusers import AutoPipelineForInpainting, DPMSolverMultistepScheduler

print("diffusers import OK")

# %% [markdown]
# ## Cellule 4 - Imports et configuration

# %%
import io
import json
import math
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from rembg import new_session, remove

ROOT = Path("/content/cosmetic_ai_service")
INPUT_DIR = ROOT / "inputs"
OUTPUT_DIR = ROOT / "outputs"
for directory in [INPUT_DIR, OUTPUT_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

FORMATS = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "linkedin": (1200, 627),
}

CATEGORY_STYLES = {
    "soin_visage": "white spa skincare set, marble surface, pearl reflections, clean dermocosmetic premium mood",
    "soin_corps": "warm natural body care set, wooden surface, shea butter texture, botanical leaves, soft warm daylight",
    "cheveux": "clean salon and bathroom set, silk texture, botanical oil drops, fresh diffused daylight",
    "maquillage": "high-end beauty editorial set, satin fabric, rose gold highlights, glamorous studio lighting",
    "solaire": "sunny beach campaign set, turquoise water blur, clean sand texture, tropical leaves, golden sunlight",
    "hygiene_deo": "fresh hygiene campaign set, clean white bathroom, eucalyptus leaves, water droplets, cool blue tones",
    "nail_care": "fresh Atlantic nail care campaign set, clear blue sky, sea water reflections, white stone, blue and white palette",
}

NEGATIVE_PROMPT = (
    "text, letters, watermark, logo, fake product, extra bottle, duplicated packaging, "
    "deformed object, distorted label, blurry, low quality, cartoon, illustration, "
    "messy background, overcrowded composition, hands, people, face"
)

# %% [markdown]
# ## Cellule 5 - Fonctions image

# %%
def preprocess_image(image_bytes: bytes, max_side: int = 1536) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")
    if max(image.size) > max_side:
        ratio = max_side / max(image.size)
        image = image.resize(
            (round(image.width * ratio), round(image.height * ratio)),
            Image.LANCZOS,
        )
    return image


def remove_background_product(image: Image.Image) -> Image.Image:
    session = new_session("isnet-general-use")
    cutout = remove(
        image,
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_erode_size=5,
    )
    return cutout.convert("RGBA")


def detect_category(product_name: str, user_category: str | None = None) -> str:
    if user_category and user_category in CATEGORY_STYLES:
        return user_category

    text = product_name.lower()
    rules = {
        "nail_care": ["dissolvant", "ongle", "vernis", "nail"],
        "solaire": ["spf", "solaire", "sun", "uva", "uvb", "sunscreen"],
        "cheveux": ["shampoing", "shampoo", "cheveux", "hair", "keratine"],
        "maquillage": ["mascara", "rouge a levres", "gloss", "blush", "makeup", "fond de teint"],
        "hygiene_deo": ["deodorant", "deo", "gel douche", "savon", "hygiene"],
        "soin_corps": ["lait corps", "karite", "body", "corps", "beurre"],
        "soin_visage": ["serum", "creme visage", "visage", "anti age", "hydratant"],
    }
    for category, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "soin_visage"


def build_prompt(category: str, palette: str, mood: str) -> str:
    style = CATEGORY_STYLES.get(category, CATEGORY_STYLES["soin_visage"])
    return (
        f"professional cosmetic product advertisement background, {style}, "
        f"{palette} color palette, {mood}, realistic commercial photography, "
        f"soft premium lighting, elegant surface, clean negative space for text, "
        f"high-end beauty campaign, photorealistic, sharp, studio quality"
    )


def prepare_inpaint_inputs(product_rgba: Image.Image, size: tuple[int, int] = (1024, 1024)):
    base = Image.new("RGBA", size, (255, 255, 255, 255))
    ratio = min(size[0] * 0.46 / product_rgba.width, size[1] * 0.70 / product_rgba.height)
    product_size = (round(product_rgba.width * ratio), round(product_rgba.height * ratio))
    product = product_rgba.resize(product_size, Image.LANCZOS)

    x = (size[0] - product.width) // 2
    y = size[1] - product.height - round(size[1] * 0.08)
    base.alpha_composite(product, (x, y))

    product_alpha = Image.new("L", size, 0)
    product_alpha.paste(product.getchannel("A"), (x, y))
    protected = product_alpha.filter(ImageFilter.MaxFilter(11))

    mask = Image.new("L", size, 255)
    mask.paste(0, mask=protected)

    return base.convert("RGB"), mask, product, (x, y)


def add_product_shadow(canvas: Image.Image, product: Image.Image, xy: tuple[int, int]) -> Image.Image:
    x, y = xy
    rgba = canvas.convert("RGBA")
    shadow_layer = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    alpha = product.getchannel("A").filter(ImageFilter.GaussianBlur(24))
    shadow = Image.new("RGBA", product.size, (0, 0, 0, 105))
    shadow.putalpha(alpha)
    shadow_layer.alpha_composite(shadow, (x + 16, y + 26))
    return Image.alpha_composite(rgba, shadow_layer)


def compose_scene(decor: Image.Image, product: Image.Image, xy: tuple[int, int]) -> Image.Image:
    scene = add_product_shadow(decor, product, xy)
    scene.alpha_composite(product, xy)
    scene = ImageEnhance.Contrast(scene.convert("RGB")).enhance(1.06)
    scene = ImageEnhance.Color(scene).enhance(1.04)
    return ImageEnhance.Brightness(scene).enhance(1.02)

# %% [markdown]
# ## Cellule 6 - Chargement SDXL corrige
# Cette cellule remplace ton ancienne cellule 6. Pas de xformers au debut.

# %%
pipe = AutoPipelineForInpainting.from_pretrained(
    "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
    torch_dtype=torch.float16,
    variant="fp16",
    use_safetensors=True,
).to("cuda")

pipe.scheduler = DPMSolverMultistepScheduler.from_config(
    pipe.scheduler.config,
    use_karras_sigmas=True,
)
pipe.enable_vae_slicing()

print("SDXL Inpainting charge correctement")

# %%
def generate_decor(base_image: Image.Image, mask: Image.Image, prompt: str, seed: int = 42) -> Image.Image:
    generator = torch.Generator("cuda").manual_seed(seed)
    with torch.inference_mode():
        return pipe(
            prompt=prompt,
            negative_prompt=NEGATIVE_PROMPT,
            image=base_image,
            mask_image=mask,
            num_inference_steps=28,
            guidance_scale=6.8,
            strength=0.99,
            generator=generator,
        ).images[0]

# %% [markdown]
# ## Cellule 7 - Texte marketing
# Ollama est optionnel. Si Ollama ne marche pas, le service utilise un fallback propre.

# %%
def start_ollama_optional():
    try:
        import subprocess

        install_check = os.system("which ollama > /dev/null 2>&1")
        if install_check != 0:
            os.system("curl -fsSL https://ollama.com/install.sh | sh")

        subprocess.Popen(
            ["/usr/local/bin/ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(5)
        requests.get("http://localhost:11434/api/tags", timeout=4)
        os.system("ollama pull qwen2.5:7b-instruct-q4_K_M")
        print("Ollama pret")
        return True
    except Exception as exc:
        print("Ollama indisponible, fallback texte active:", exc)
        return False


OLLAMA_READY = False


def fallback_text(product_name: str, brand: str, category: str, tone: str) -> dict:
    label = f"{brand} {product_name}".strip() or "Votre rituel beaute"
    category_bullets = {
        "nail_care": ["Respecte vos ongles", "Sensation de douceur", "Finition nette"],
        "solaire": ["Protection au quotidien", "Texture agreable", "Esprit vacances"],
        "cheveux": ["Cheveux sublimes", "Routine sensorielle", "Brillance naturelle"],
        "maquillage": ["Couleur intense", "Fini elegant", "Look affirme"],
        "hygiene_deo": ["Fraicheur durable", "Gestuelle simple", "Confort quotidien"],
        "soin_corps": ["Peau douce", "Texture nourrissante", "Moment cocooning"],
        "soin_visage": ["Eclat naturel", "Routine premium", "Peau confortable"],
    }
    return {
        "titre": label[:42],
        "sous_titre": "Une affiche premium prete pour vos reseaux sociaux.",
        "bullets": category_bullets.get(category, category_bullets["soin_visage"]),
        "cta": "Decouvrir",
        "hashtags": ["#beaute", "#cosmetique", "#skincare", "#beauty", "#routine"],
    }


def generate_marketing_text(product_name: str, brand: str, category: str, tone: str) -> dict:
    if not OLLAMA_READY:
        return fallback_text(product_name, brand, category, tone)

    system_prompt = (
        "Tu es un copywriter senior specialise en cosmetique. "
        "Reponds uniquement en JSON valide. "
        "N'invente aucune promesse medicale, aucun resultat clinique et aucun ingredient absent. "
        "Le texte doit etre court et lisible sur une affiche mobile."
    )
    user_prompt = f"""
Produit: {brand} {product_name}
Categorie: {category}
Ton: {tone}

Retourne exactement:
{{
  "titre": "max 6 mots",
  "sous_titre": "max 14 mots",
  "bullets": ["benefice 1", "benefice 2", "benefice 3"],
  "cta": "2 a 4 mots",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]
}}
"""
    try:
        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "qwen2.5:7b-instruct-q4_K_M",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "format": "json",
                "stream": False,
            },
            timeout=120,
        )
        raw = response.json()["message"]["content"]
        return json.loads(raw)
    except Exception as exc:
        print("Erreur Qwen, fallback texte:", exc)
        return fallback_text(product_name, brand, category, tone)

# %% [markdown]
# ## Cellule 8 - Rendu affiche et ZIP

# %%
def load_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def fit_text(draw: ImageDraw.ImageDraw, text: str, font_path_size: int, max_width: int, bold: bool = False):
    size = font_path_size
    while size >= 18:
        font = load_font(size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 2
    return load_font(18, bold=bold)


def render_poster(scene: Image.Image, text: dict, output_size: tuple[int, int]) -> Image.Image:
    poster = ImageOps.fit(scene, output_size, method=Image.LANCZOS, centering=(0.5, 0.5)).convert("RGBA")
    draw = ImageDraw.Draw(poster)
    width, height = output_size

    band_h = round(height * 0.25)
    band = Image.new("RGBA", (width, band_h), (255, 255, 255, 226))
    poster.alpha_composite(band, (0, height - band_h))

    margin_x = round(width * 0.055)
    start_y = height - band_h + round(height * 0.035)
    max_text_width = round(width * 0.62)

    title = text.get("titre", "Votre rituel beaute")
    subtitle = text.get("sous_titre", "")
    bullets = text.get("bullets", [])[:3]
    cta = text.get("cta", "Decouvrir")

    title_font = fit_text(draw, title, round(height * 0.058), max_text_width, bold=True)
    subtitle_font = load_font(max(18, round(height * 0.028)))
    bullet_font = load_font(max(15, round(height * 0.022)))
    cta_font = load_font(max(18, round(height * 0.026)), bold=True)

    draw.text((margin_x, start_y), title, font=title_font, fill=(20, 35, 65))
    draw.text((margin_x, start_y + round(height * 0.067)), subtitle, font=subtitle_font, fill=(55, 70, 92))

    bullet_y = start_y + round(height * 0.115)
    for index, bullet in enumerate(bullets):
        y = bullet_y + index * round(height * 0.033)
        draw.ellipse((margin_x, y + 7, margin_x + 8, y + 15), fill=(22, 87, 172))
        draw.text((margin_x + 18, y), bullet, font=bullet_font, fill=(35, 60, 98))

    box_w = min(round(width * 0.30), 265)
    box_h = max(44, round(height * 0.052))
    box = [
        width - margin_x - box_w,
        height - round(height * 0.078),
        width - margin_x,
        height - round(height * 0.078) + box_h,
    ]
    draw.rounded_rectangle(box, radius=10, fill=(22, 87, 172))
    draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), cta.upper(), font=cta_font, fill="white", anchor="mm")

    return poster.convert("RGB")


def build_zip(posters: dict[str, Image.Image], metadata: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, image in posters.items():
            image_buffer = io.BytesIO()
            image.save(image_buffer, format="JPEG", quality=95)
            archive.writestr(f"affiche_{name}.jpg", image_buffer.getvalue())
        archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))
    buffer.seek(0)
    return buffer.getvalue()

# %% [markdown]
# ## Cellule 9 - Pipeline principal

# %%
def generate_campaign_zip(
    image_bytes: bytes,
    product_name: str,
    brand: str = "",
    category: str = "",
    tone: str = "luxe",
    palette: str = "blue, white, clean premium",
    mood: str = "fresh elegant atmosphere",
    seed: int = 42,
) -> bytes:
    image = preprocess_image(image_bytes)
    product_rgba = remove_background_product(image)
    detected_category = detect_category(product_name, category)
    prompt = build_prompt(detected_category, palette, mood)
    base, mask, product, xy = prepare_inpaint_inputs(product_rgba)
    decor = generate_decor(base, mask, prompt, seed=seed)
    scene = compose_scene(decor, product, xy)
    marketing = generate_marketing_text(product_name, brand, detected_category, tone)

    posters = {
        name: render_poster(scene, size)
        for name, size in FORMATS.items()
    }
    metadata = {
        "product_name": product_name,
        "brand": brand,
        "category": detected_category,
        "tone": tone,
        "palette": palette,
        "mood": mood,
        "seed": seed,
        "prompt": prompt,
        "marketing_text": marketing,
        "formats": FORMATS,
    }
    return build_zip(posters, metadata)

# %% [markdown]
# ## Cellule 10 - Test local dans Colab

# %%
# Exemple avec upload manuel:
# from google.colab import files
# uploaded = files.upload()
# filename = next(iter(uploaded))
# zip_bytes = generate_campaign_zip(
#     uploaded[filename],
#     product_name="Nihel Dissolvant doux a l'Atlantic",
#     brand="Nihel",
#     category="nail_care",
#     tone="frais",
#     palette="blue and white Atlantic freshness",
#     mood="ocean inspired clean beauty campaign",
# )
# Path("/content/affiches_test.zip").write_bytes(zip_bytes)
# files.download("/content/affiches_test.zip")

# %% [markdown]
# ## Cellule 11 - API FastAPI + ngrok
# C'est cette URL que ton backend local doit appeler.

# %%
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import Response
from pyngrok import ngrok
import nest_asyncio
import uvicorn

NGROK_TOKEN = "COLLE_TON_TOKEN_NGROK_ICI"

app = FastAPI(title="Cosmetic AI Poster Service")


@app.get("/health")
def health():
    return {"status": "ok", "gpu": torch.cuda.is_available()}


@app.post("/generate-campaign")
async def generate_campaign(
    image: UploadFile = File(...),
    product_name: str = Form(...),
    brand: str = Form(""),
    category: str = Form(""),
    tone: str = Form("luxe"),
    palette: str = Form("blue, white, clean premium"),
    mood: str = Form("fresh elegant atmosphere"),
    seed: int = Form(42),
):
    image_bytes = await image.read()
    zip_bytes = generate_campaign_zip(
        image_bytes=image_bytes,
        product_name=product_name,
        brand=brand,
        category=category,
        tone=tone,
        palette=palette,
        mood=mood,
        seed=seed,
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="affiches_cosmetiques.zip"'},
    )


if NGROK_TOKEN == "COLLE_TON_TOKEN_NGROK_ICI":
    print("Remplace NGROK_TOKEN avant de lancer le serveur public.")
else:
    ngrok.set_auth_token(NGROK_TOKEN)
    public_url = ngrok.connect(8000).public_url
    print("Service IA public:", public_url)
    print("Endpoint:", public_url + "/generate-campaign")
    nest_asyncio.apply()
    uvicorn.run(app, host="0.0.0.0", port=8000)
