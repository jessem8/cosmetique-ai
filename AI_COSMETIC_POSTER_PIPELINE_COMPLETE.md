# Pipeline IA complet - Generateur d'affiches publicitaires cosmetiques

Ce document est le plan de travail complet pour la partie IA du projet. L'objectif est simple a expliquer mais exigeant a executer:

> L'utilisateur envoie une photo brute d'un produit cosmetique, prise avec n'importe quel fond. Le systeme detoure le produit, comprend son etiquette, cree un decor publicitaire coherent, genere le texte marketing, compose l'affiche, puis exporte les formats Instagram, Facebook et LinkedIn.

## 1. Decision technique importante

Avec 220 photos produit, il ne faut pas entrainer un modele generatif complet. Ce serait couteux, instable, et le modele risquerait d'apprendre les produits de la dataset au lieu de devenir generalisable.

La bonne approche pour un resultat robuste est:

- utiliser la dataset pour tester, nettoyer, benchmarker et ameliorer les prompts;
- utiliser des modeles pre-entraines pour la generation;
- garder le produit original intact par detourage + inpainting;
- utiliser OCR + champs utilisateur pour eviter les textes inventes;
- utiliser des templates de composition fiables pour les reseaux sociaux.

La dataset devient donc un jeu de validation professionnel, pas la matiere principale d'un entrainement lourd.

## 2. Architecture cible

Flux de production:

1. Upload produit depuis le frontend.
2. Sauvegarde du produit en backend.
3. Creation d'un job `Generation` avec statut `pending`.
4. Preprocessing image: orientation EXIF, conversion RGB, resize.
5. Detourage produit avec `rembg` / `isnet-general-use`.
6. OCR etiquette avec PaddleOCR.
7. Detection categorie: mots-cles + fallback LLM.
8. Construction prompt visuel selon categorie, couleur dominante, saison, ton.
9. Generation decor avec SDXL Inpainting, en preservant le produit.
10. Composition: produit + ombre + correction couleur.
11. Generation texte marketing JSON avec Qwen2.5 / Ollama.
12. Rendu final avec Pillow.
13. Export 3 formats: Instagram, Facebook, LinkedIn.
14. Upload Cloudinary ou stockage local.
15. Frontend affiche les resultats et propose le telechargement ZIP.

## 3. Dataset: organisation et preprocessing

Dataset actuelle:

```text
D:\Stage_1_ouvrier\data\raw\product_photos
```

Sortie conseillee:

```text
D:\Stage_1_ouvrier\data\processed\product_photos
```

Commande locale:

```powershell
cd D:\Stage_1_ouvrier
python ai\scripts\preprocess_dataset.py --source data\raw\product_photos --output data\processed\product_photos --max-side 1280
```

Option recommandee pour detecter les doublons perceptuels:

```powershell
pip install imagehash
```

Ce que fait le preprocessing:

- corrige l'orientation EXIF;
- convertit toutes les images en RGB;
- limite le grand cote a 1280 px;
- exporte en JPEG qualite 94;
- calcule un SHA256 pour les doublons exacts;
- calcule un perceptual hash si `imagehash` est disponible;
- produit `preprocessing_report.json`.

Ce qu'il ne faut pas faire:

- ne pas retourner horizontalement les images, car les etiquettes deviennent illisibles;
- ne pas augmenter artificiellement la dataset avec des rotations fortes;
- ne pas fine-tuner SDXL sur ces photos produit, car le besoin est de changer le fond tout en gardant n'importe quel produit.

## 4. Modeles recommandes

Detourage:

- `rembg` avec session `isnet-general-use`;
- `alpha_matting=True` pour les bouteilles, tubes brillants et bords blancs.

OCR:

- PaddleOCR, langues `fr`, `en`;
- l'OCR sert a extraire marque, nom produit, claims visibles, contenance, ingredients importants.

Generation image:

- SDXL Inpainting;
- option avancee: IP-Adapter SDXL pour injecter un moodboard visuel;
- ControlNet Canny optionnel si la composition doit respecter une silhouette stricte.

Texte:

- Qwen2.5 via Ollama en local/Colab;
- sortie JSON stricte;
- interdiction d'inventer des claims medicaux ou dermatologiques non fournis.

## 5. Prompt image

Template general:

```text
professional cosmetic product advertisement background, {category_style},
{palette} color palette, soft realistic commercial lighting,
premium beauty campaign, elegant surface, clean negative space for text,
photorealistic, studio quality, high-end advertising photography
```

Negative prompt:

```text
text, letters, watermark, logo, fake product, extra bottle, deformed object,
distorted packaging, blurry, low quality, cartoon, illustration,
messy background, overcrowded composition, bad typography
```

Categories conseillees:

- `soin_visage`: spa blanc, marbre, lumiere douce, ambiance dermocosmetique;
- `soin_corps`: bois clair, beurre de karite, plantes, chaleur naturelle;
- `cheveux`: salle de bain propre, soie, botanique, lumiere fraiche;
- `maquillage`: satin, rose gold, reflets, editorial beauty;
- `solaire`: plage, eau turquoise, lumiere chaude;
- `hygiene_deo`: salle de bain clean, eucalyptus, gouttes d'eau;
- `nail_care`: salon elegant, bleu/frais si dissolvant, mains stylisees seulement si coherent.

Exemple adapte au produit Nihel:

```text
professional cosmetic nail care advertisement background, fresh Atlantic ocean mood,
clear blue sky, clean sea water reflections, white stone surface, olive leaves,
blue and white palette, soft daylight, premium cosmetic campaign,
clean negative space for text, photorealistic commercial photography
```

## 6. Prompt texte marketing

System prompt:

```text
Tu es un copywriter senior specialise en cosmetique. Reponds uniquement en JSON valide.
N'invente aucune promesse medicale, aucun resultat clinique et aucun ingredient absent des donnees.
Le texte doit etre court, lisible sur mobile et pret pour une affiche reseaux sociaux.
```

User prompt:

```text
Produit: {brand} {product_name}
Categorie: {category}
Texte OCR etiquette: {ocr_text}
Ton: {tone}
Plateforme: {platform}

Retourne exactement:
{
  "titre": "max 6 mots",
  "sous_titre": "max 14 mots",
  "bullets": ["benefice 1", "benefice 2", "benefice 3"],
  "cta": "2 a 4 mots",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]
}
```

Regle importante: le texte OCR est la source de verite. Si l'etiquette dit seulement "soin des ongles, provitamine B5, douceur intense", le modele ne doit pas inventer "renforce en 7 jours" ou "teste dermatologiquement".

## 7. Google Colab: ordre des cellules

1. Activer GPU T4.
2. Installer dependances: torch, diffusers, transformers, accelerate, rembg, onnxruntime-gpu, paddleocr, pillow, fastapi.
3. Monter Google Drive si tu veux sauvegarder les sorties.
4. Uploader un produit test.
5. Detourer le produit.
6. Lancer OCR.
7. Construire le canvas + masque.
8. Charger SDXL Inpainting.
9. Generer le decor.
10. Composer l'affiche.
11. Generer le texte avec Ollama/Qwen.
12. Exporter les 3 formats.
13. Option demo: exposer FastAPI via ngrok.

Un script Colab executable est fourni ici:

```text
ai/colab_cosmetic_poster_pipeline.py
```

Dans Colab, tu peux copier les cellules du fichier ou l'ouvrir comme script avec cellules `# %%`.

## 8. Integration dans ton projet

Etat actuel du projet:

- backend FastAPI avec `Generation`, `Asset`, routes de polling et ZIP;
- frontend Vite/React avec page generation et page resultat;
- pipeline Python central dans `backend/app/services/pipeline.py`.

Integration recommandee pour la soutenance:

- local: backend/frontend tournent sur ta machine;
- Colab: service IA GPU expose avec ngrok;
- backend local appelle l'URL Colab pour la generation lourde;
- backend garde la base, les utilisateurs, les assets et le telechargement.

Integration recommandee plus tard:

- remplacer Colab par RunPod, Modal, Replicate private deployment, ou serveur GPU;
- garder la meme interface HTTP;
- ne pas changer le frontend.

## 9. Checklist qualite

Une affiche est publiable si:

- le produit est net et non deforme;
- l'etiquette reste lisible;
- il n'y a pas de texte hallucine dans le decor;
- les bords du detourage n'ont pas de halo visible;
- l'ombre est coherente avec la lumiere;
- le texte marketing ne fait pas de promesse inventee;
- les formats ne coupent pas le produit;
- le contraste est lisible sur mobile;
- le fichier final se telecharge en local depuis l'interface.

## 10. Plan de travail conseille

Semaine IA:

1. Nettoyer la dataset avec `preprocess_dataset.py`.
2. Choisir 20 images tests representatives: tube, flacon, pot, transparent, blanc sur fond blanc, produit tenu en main.
3. Tester le detourage sur ces 20 images.
4. Corriger les prompts par categorie.
5. Tester SDXL sur 5 produits.
6. Ajouter OCR.
7. Generer les textes JSON.
8. Composer les affiches.
9. Brancher Colab au backend via ngrok.
10. Tester le flow complet frontend: upload, generation, resultat, telechargement ZIP.

Priorite absolue pour impressionner l'utilisateur: preservation du produit original. Le decor peut varier, le texte peut etre regenere, mais le packaging ne doit pas etre hallucine.
