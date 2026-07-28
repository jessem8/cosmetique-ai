# Guide d'execution IA - Affiches publicitaires cosmetiques

## 1. Objectif du module IA

Le module IA doit transformer une photo brute d'un produit cosmetique, prise avec n'importe quel fond, en affiche publicitaire publiable sur Instagram, Facebook et LinkedIn.

L'objectif n'est pas d'entrainer un modele pour reconnaitre un produit precis. Le bon choix pour ton projet est un pipeline zero-shot/modulaire:

1. normaliser l'image utilisateur;
2. detourer le produit;
3. lire les informations visibles ou fournies par metadata;
4. generer un decor premium adapte a la categorie;
5. recomposer le vrai produit dans ce decor;
6. generer un texte marketing sans inventer de promesses;
7. exporter plusieurs formats reseaux sociaux;
8. journaliser les prompts, seeds, parametres et resultats.

## 2. Etat actuel du projet

Le dossier contient deja une bonne base:

- `data/raw/product_photos/`: 51 images produit.
- `data/metadata/products.csv`: metadata produit validee.
- `ai/prompts/`: prompts image, captions et regles visuelles.
- `ai/scripts/`: scripts de detourage, prompt builder, composition et validation.
- `notebook/pipeline_ia_cosmetique.ipynb`: notebook Colab existant.

Validation dataset actuelle:

- 51 lignes verifiees.
- 0 erreur bloquante.
- avertissements principaux: presque toutes les lignes du CSV indiquent `.jpg` alors que les fichiers sont en `.jpeg`; c'est accepte par le validateur, mais il faut harmoniser avant la soutenance.
- quelques `tone` n'ont pas encore de style copywriting dedie: `soft`.

## 3. Dataset: collecte de 51 vers 300 images

### 3.1 Repartition cible

Pour 300 images, vise cette repartition:

| Famille | Nombre cible | Exemples |
|---|---:|---|
| Skincare visage/corps | 80 | creme, serum, lait, gel, huile |
| Parapharmacie/dermocosmetique | 60 | solaire, acne, peau sensible, baume |
| Hygiene/beaute quotidienne | 50 | deodorant, gel douche, lingettes |
| Cheveux | 40 | shampoing, apres-shampoing, masque |
| Maquillage | 35 | rouge a levres, mascara, fond de teint |
| Parfum | 25 | flacon verre, coffret, brume |
| Ongles/accessoires | 10 | dissolvant, soin ongles |

Le dataset doit representer le vrai cas utilisateur: photos imparfaites, fonds varies, lumiere normale, produit parfois incline. Ne collecte pas seulement des photos trop propres.

### 3.2 Regles de prise de photo

- Le produit entier doit etre visible.
- Un seul produit par image pour le MVP.
- Garder le texte de l'etiquette aussi net que possible.
- Eviter les mains devant le produit.
- Eviter les reflets extremes sur flacons brillants.
- Garder la photo originale sans la modifier.
- Ne pas retourner horizontalement une photo avec texte: cela casse l'OCR.

### 3.3 Metadata obligatoire

Chaque photo doit avoir une ligne dans `data/metadata/products.csv`.

Colonnes importantes:

- `id`: meme identifiant que l'image, exemple `p052`.
- `image_file`: nom exact du fichier, exemple `p052.jpeg`.
- `brand`: marque.
- `name`: nom produit.
- `category`: categorie normalisee.
- `dominant_color`: couleur dominante du packaging.
- `secondary_color`: couleur secondaire.
- `season`: ambiance commerciale, exemple `summer`, `neutral_luxury`, `all-season`.
- `tone`: style du texte, exemple `luxury`, `fresh`, `clinical`, `gentle`.
- `benefits`: benefices reels ou visibles.
- `ingredients`: ingredients visibles ou connus.
- `price`, `promotion`, `cta`.

## 4. Preprocessing images

### 4.1 Standardisation

Pour chaque image:

1. corriger l'orientation EXIF;
2. convertir en RGB;
3. limiter le grand cote a 1600 px pour le stockage de travail;
4. garder un original intact dans `data/raw/product_photos/`;
5. sauvegarder les versions preparees dans `data/processed/resized_products/`.

### 4.2 Dedoublonnage

Utiliser un hash perceptuel (`imagehash.phash`) pour detecter:

- memes photos avec extensions differentes;
- copies compressees;
- images tres proches.

Garder la photo la plus nette et la mieux exposee.

### 4.3 Detourage

Modele recommande:

- `rembg` avec session `isnet-general-use`;
- `alpha_matting=True`;
- seuils ajustes pour eviter les halos.

Sortie:

- PNG RGBA transparent dans `data/processed/cutouts/`.

Controle qualite detourage:

- bord propre;
- pas de trou dans le produit;
- pas de fond residuel;
- etiquette lisible;
- pas de halo blanc/noir autour des flacons.

## 5. Pipeline image recommande sur Colab GPU

### 5.1 Choix modele

Pour ton objectif, utilise:

- SDXL Inpainting pour creer uniquement le fond;
- IP-Adapter SDXL pour guider le style avec une image moodboard;
- ControlNet Canny optionnel pour garder une composition stable;
- Pillow pour la composition finale.

Ne commence pas par un fine-tuning LoRA. Il faut d'abord prouver que le pipeline zero-shot fonctionne sur plusieurs produits. Le LoRA vient plus tard si tu veux un style proprietaire.

### 5.2 Process complet

1. Upload produit.
2. Nettoyage image.
3. Detourage produit.
4. OCR ou metadata pour extraire marque, nom, benefices, ingredients.
5. Creation d'un canvas au format cible.
6. Placement du produit avec masque de preservation.
7. SDXL Inpainting genere le decor autour du produit.
8. Post-traitement: ombre, reflet si necessaire, color grading leger.
9. Texte marketing JSON.
10. Composition finale.
11. Export Instagram/Facebook/LinkedIn.

### 5.3 Formats finals

| Plateforme | Format final | Usage |
|---|---:|---|
| Instagram carre | 1080x1080 | post classique |
| Instagram portrait/Facebook | 1080x1350 | meilleur impact mobile |
| Facebook lien | 1200x630 | partage horizontal |
| LinkedIn | 1200x627 ou 1200x628 | post professionnel |

Ton code actuel utilise:

- instagram: `1080x1080`;
- facebook: `1080x1350`;
- linkedin: `1200x628`.

C'est coherent pour une demo.

## 6. Prompts image

### 6.1 Prompt global

Structure recommandee:

```text
professional cosmetic product advertisement background,
{category_visual_style},
{color_palette},
{season_or_campaign_style},
premium studio lighting,
clean composition,
negative space for typography,
photorealistic,
high-end beauty campaign,
no text, no logo, no product
```

### 6.2 Negative prompt global

```text
text, letters, watermark, logo, fake product, extra product,
distorted packaging, bad typography, blurry, low quality,
cartoon, illustration, cluttered background, deformed shapes
```

### 6.3 Templates par categorie

Skincare:

```text
minimalist spa setting, marble surface, soft natural light,
botanical elements, cream texture, dermocosmetic campaign look
```

Parfum:

```text
luxury perfume advertisement, reflective glass surface,
golden rim light, flower petals, cinematic lighting,
elegant dark or champagne background
```

Maquillage:

```text
high-fashion beauty editorial, satin fabric, glossy highlights,
bold color-blocked backdrop, premium makeup campaign
```

Cheveux:

```text
fresh salon setting, soft daylight, water droplets,
natural wood or stone, clean haircare campaign
```

Solaire:

```text
sunny beach or poolside setting, warm golden light,
sand texture, turquoise water blur, tropical leaves
```

Hygiene/deodorant:

```text
fresh clean lifestyle background, bright studio light,
water droplets, clean bathroom or sport freshness mood
```

## 7. Texte marketing

### 7.1 Modele recommande

Pour Colab:

- Qwen2.5 7B Instruct quantifie via Ollama si possible;
- sinon Qwen2.5 3B pour economiser RAM/VRAM;
- sortie JSON stricte.

### 7.2 Regle importante

Le modele ne doit jamais inventer de promesse medicale ou cosmetique non presente dans:

- OCR etiquette;
- metadata `products.csv`;
- texte fourni par l'utilisateur.

### 7.3 JSON attendu

```json
{
  "headline": "Eclat naturel",
  "subtitle": "Un soin doux pour votre routine quotidienne",
  "cta": "Commander",
  "instagram_caption": "...",
  "facebook_caption": "...",
  "linkedin_caption": "...",
  "hashtags": ["#Skincare", "#Beauty", "#Routine"]
}
```

### 7.4 Prompt systeme

```text
Tu es un copywriter specialise en cosmetique et parapharmacie.
Tu ecris en francais clair, elegant et credible.
Tu ne dois pas inventer de claims medicaux.
Tu utilises uniquement les informations produit fournies.
Tu reponds seulement en JSON valide.
```

## 8. Evaluation qualite

Une affiche est publiable seulement si:

- le produit reel est conserve et non deforme;
- le fond est premium, propre et coherent avec la categorie;
- aucun faux texte n'apparait dans le fond;
- le produit ne flotte pas: ombre/reflet coherent;
- le texte est lisible sur mobile;
- la marque et le nom produit ne sont pas faux;
- le CTA est clair;
- les formats exports ne deformant pas le produit;
- le seed, prompt et negative prompt sont sauvegardes.

Notation conseillee sur 5:

| Critere | Poids |
|---|---:|
| Detourage | 20% |
| Realisme du decor | 20% |
| Integration produit/decor | 20% |
| Lisibilite texte | 15% |
| Pertinence marketing | 15% |
| Respect format reseau social | 10% |

Objectif soutenance: obtenir au moins 4/5 sur 10 produits varies.

## 9. Integration backend/frontend

### 9.1 Pour la demo

Architecture simple:

1. Colab lance FastAPI + ngrok.
2. Backend ou frontend appelle l'URL ngrok.
3. Colab retourne l'image finale.

Cette option est bonne pour la demo, mais pas pour production.

### 9.2 Pour une version propre

Architecture recommandee:

1. Frontend upload la photo.
2. Backend cree un job en base: `pending`.
3. Worker GPU traite le job: `running`.
4. Worker sauvegarde les assets: `completed`.
5. Frontend poll le statut toutes les 3 secondes.

Cette architecture evite les timeouts HTTP longs et correspond mieux a une vraie app.

## 10. Roadmap de travail

### Sprint 1 - Stabiliser data et preprocessing

- harmoniser `.jpg` / `.jpeg` dans `products.csv`;
- ajouter les tones manquants dans `caption_prompts.json`;
- generer les cutouts pour 10 produits test;
- evaluer les erreurs de detourage.

### Sprint 2 - Notebook Colab complet

- installer dependencies;
- charger dataset depuis Drive;
- executer detourage;
- generer decor SDXL pour 3 categories;
- composer les 3 formats;
- sauvegarder logs JSON.

### Sprint 3 - Prompt engineering

- creer 6 familles de prompts;
- tester 3 seeds par produit;
- garder le meilleur resultat;
- documenter les prompts gagnants.

### Sprint 4 - Texte marketing

- generer headline, subtitle, CTA et captions;
- imposer JSON strict;
- verifier absence de claims inventes.

### Sprint 5 - Integration app

- endpoint IA temporaire Colab/ngrok;
- branchement frontend;
- ecran loader avec statut;
- page resultat avec download des 3 formats.

### Sprint 6 - Qualite soutenance

- selectionner 10 produits demonstrateurs;
- produire avant/apres;
- preparer tableau d'evaluation;
- enregistrer seeds/prompts pour reproduire.

## 11. Priorites immediates

1. Corriger les extensions dans `products.csv` ou renommer les fichiers pour supprimer les warnings.
2. Ajouter le tone `soft` dans `ai/prompts/caption_prompts.json`.
3. Creer un script batch pour detourer les 51 produits.
4. Tester le notebook Colab sur 3 produits tres differents:
   - un flacon blanc/simple;
   - un produit transparent/brillant;
   - un produit colore.
5. Mettre en place un fichier log par generation:

```json
{
  "product_id": "p001",
  "prompt": "...",
  "negative_prompt": "...",
  "seed": 42,
  "platform": "instagram",
  "output_path": "...",
  "quality_score": null
}
```

## 12. Ce qu'il ne faut pas faire maintenant

- Ne pas entrainer un LoRA tout de suite.
- Ne pas utiliser des pubs Instagram/Pinterest comme dataset d'entrainement.
- Ne pas generer le produit lui-meme avec SDXL: il faut garder le vrai produit.
- Ne pas laisser le modele texte inventer des ingredients ou promesses.
- Ne pas juger le pipeline sur un seul produit facile.

