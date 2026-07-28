# Guide Dataset - Premium Cosmetics

## Combien collecter

Minimum pour un bon debut:

- 50 photos produit pour MVP solide;
- 100 photos si tu veux tester beaucoup de cas;
- 200+ photos seulement si tu veux preparer un vrai LoRA plus tard.

## Photos avec ou sans background

Collecte surtout des photos avec background reel:

- produit sur table;
- produit dans une boutique;
- produit tenu a la main;
- produit devant un mur;
- produit avec lumiere moyenne;
- produit legerement incline.

Pourquoi: ton application doit marcher pour un utilisateur normal qui prend une photo rapidement. `rembg` doit donc etre teste sur des backgrounds varies.

Garde aussi quelques images propres sans fond si tu en as, mais seulement comme reference de qualite.

## Regles de prise de photo

- Une photo par produit.
- Produit entier visible.
- Eviter que le produit soit coupe.
- Eviter les reflets trop forts sur le texte.
- Garder une resolution correcte.
- Ne pas mettre plusieurs produits dans la meme photo pour le MVP.

## Nommage

Renomme toutes les images:

```text
p001.jpg
p002.jpg
p003.jpg
```

Place-les ici:

```text
data/raw/product_photos/
```

## Metadata obligatoire

Chaque image doit avoir une ligne dans:

```text
data/metadata/products.csv
```

Champs importants:

- `id`: meme ID que le nom image;
- `image_file`: exemple `p001.jpg`;
- `brand`: marque;
- `name`: nom produit;
- `category`: serum, cream, perfume, shampoo, oil, sunscreen, makeup, parapharmacy;
- `dominant_color`: pink, red, white, black, gold, green, blue, purple, orange, transparent, silver, brown;
- `season`: neutral_luxury, summer, winter, ramadan, valentine, spring, autumn, eid, black_friday;
- `tone`: luxury, professional, fresh, romantic, clinical.

## Sources possibles

1. Tes propres photos: meilleur choix.
2. Produits de maison/parapharmacie/parfumerie: tres utile pour tester.
3. Open Beauty Facts: utile pour noms, categories, ingredients et quelques images.
4. Images libres de droits: seulement si la licence est claire.

Ne pas utiliser Pinterest, Instagram ou publicites de marques comme dataset d'entrainement.

## Checklist collecte

- 10 produits roses/rouges.
- 10 produits blancs/transparents.
- 10 produits verts/bleus.
- 10 produits noirs/dores.
- 10 produits mixtes.
- Au moins 5 parfums.
- Au moins 10 produits skincare.
- Au moins 5 produits parapharmacie.

