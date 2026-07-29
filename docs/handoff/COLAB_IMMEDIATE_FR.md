# Colab — démarrage immédiat (T4)

Fais ces étapes **maintenant**, sans attendre. Le tunnel Cloudflare expire dès que Colab s’arrête.

## 1. Ouvrir le notebook

1. Ouvre `notebook/pipeline_ia_cosmetique.ipynb` (repo Cosmetique AI / Drive du projet).
2. Runtime → **Changer le type d’exécution** → GPU **T4**.
3. Runtime → **Tout exécuter**.

## 2. Attendre « ready »

Quand le service est prêt, le notebook affiche **une seule fois** :

- l’URL `https://….trycloudflare.com`
- le **bearer token**
- le **runtime ID** (affiché pour contrôle ; le worker le lit via `/v1/health`)

Ne les mets pas dans Git, ni dans une capture d’écran publique.

## 3. Brancher Docker (machine locale)

Dans `docker/.env` (copie depuis `docker/.env.example` si besoin) :

```env
AI_SERVICE_URL=https://XXXX.trycloudflare.com
AI_SERVICE_TOKEN=…ton token…
```

Puis :

```powershell
cd <racine-du-repo>
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up -d --force-recreate backend worker
```

Ouvre `http://localhost` (ou le `APP_PORT` configuré).

## 4. Vérifier

1. Créer un compte / se connecter.
2. Uploader un produit autorisé.
3. Lancer une génération FR ou EN.
4. Télécharger le ZIP (8 fichiers : 3 JPG plateformes + copy/cutout/mask/background/manifest).

## Si Colab tombe

Message attendu côté site : `AI_RUNTIME_LOST` / service indisponible — **pas** un faux succès.

1. Nouveau runtime T4 + Tout exécuter.
2. Nouveau token + nouvelle URL.
3. Mettre à jour `docker/.env` et recreer `backend` + `worker`.

Guide détaillé : `AI_EXECUTION_GUIDE.md` et `docs/OPERATIONS.md`.
