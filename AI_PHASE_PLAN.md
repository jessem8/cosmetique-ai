# Plan Phase IA - Generateur de Contenu Premium Cosmetics

## Objectif

Construire une demo IA open source capable de transformer une photo produit brute en contenu pret pour Instagram, Facebook et LinkedIn:

- fond premium genere par IA;
- produit reel detoure et conserve;
- affiche finale composee avec texte lisible;
- captions reseaux sociaux adaptees par plateforme;
- historique des prompts et resultats.

## Decision technique

Le site n'integre pas un seul modele. Il integre un pipeline modulaire:

1. `SDXL` genere les fonds premium sans produit et sans texte.
2. `rembg` supprime le fond de la photo produit.
3. `Pillow` compose l'affiche finale: produit, ombre, logo, prix, CTA.
4. `Qwen/Ollama` genere les textes Instagram, Facebook et LinkedIn.
5. `FastAPI` orchestre le pipeline.

Le fine-tuning LoRA arrive apres le MVP, seulement si les resultats SDXL + prompts ne sont pas assez personnalises.

## Dataset attendu

Collecter au minimum 50 produits pour une premiere vraie base:

- 20 skincare: serum, creme, lotion, gel, huile;
- 10 parfums;
- 10 parapharmacie: solaire, shampooing, soin dermatologique;
- 10 produits varies: maquillage, hygiene, coffrets.

Pour chaque produit, garder la photo originale avec son background reel. Ne pas chercher uniquement des images parfaites. Le but est de tester le cas utilisateur normal: photo prise rapidement dans une boutique, maison, pharmacie ou parfumerie.

## Structure des donnees

```text
data/raw/product_photos/
  p001.jpg
  p002.jpg

data/processed/cutouts/
  p001.png
  p002.png

data/processed/posters/
  p001_instagram.png
  p001_facebook.png
  p001_linkedin.png

data/metadata/products.csv
```

## Roadmap 10 jours

### Jour 1

- creer structure IA;
- creer prompts JSON;
- creer regles couleur/saison/type produit;
- preparer `products.csv`.

### Jour 2

- collecter 50 photos produit minimum;
- renommer les images avec des IDs propres: `p001.jpg`, `p002.jpg`;
- remplir `products.csv`.

### Jour 3

- creer notebook Colab SDXL;
- tester 20 prompts premium;
- noter les meilleurs parametres.

### Jour 4

- installer/tester `rembg`;
- detourer 10 produits;
- verifier les PNG transparents.

### Jour 5

- creer composition Instagram 1080x1080;
- ajouter produit, ombre, prix, CTA.

### Jour 6

- ajouter formats Facebook et LinkedIn;
- ajuster placement, typographie et marges.

### Jour 7

- automatiser le choix du prompt selon couleur + saison + type produit.

### Jour 8

- generer captions avec Qwen/Ollama;
- separer Instagram, Facebook, LinkedIn.

### Jour 9

- creer endpoints FastAPI IA.

### Jour 10

- tester pipeline complet sur 3 produits differents:
  - produit rose;
  - produit noir/dore;
  - produit blanc ou transparent.

## Definition de fini pour la phase IA

La phase IA est consideree terminee quand:

- une photo produit brute produit automatiquement une affiche premium;
- les formats Instagram, Facebook et LinkedIn existent;
- le texte dans l'affiche est parfaitement lisible;
- les captions sont generees par plateforme;
- les prompts et resultats sont sauvegardes dans un log;
- au moins 3 types de produits donnent un resultat propre.

