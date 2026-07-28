# 🌟 Générateur de Contenu IA Cosmétique

> Pipeline IA modulaire : photo produit brute → affiches premium Instagram / Facebook / LinkedIn

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://react.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql)](https://postgresql.org)
[![Colab](https://img.shields.io/badge/Google_Colab-GPU_T4-F9AB00?logo=googlecolab)](https://colab.research.google.com)

---

## 📋 Table des matières

1. [Architecture](#architecture)
2. [Prérequis](#prérequis)
3. [Lancement Backend (local)](#lancement-backend-local)
4. [Lancement Frontend](#lancement-frontend)
5. [Exécution sur Colab](#exécution-sur-colab)
6. [Variables d'environnement](#variables-denvironnement)
7. [API Reference](#api-reference)
8. [Sécurité](#sécurité)

---

## Architecture

```
photo produit (JPEG/PNG)
        │
        ▼
   POST /products          → upload Cloudinary + DB
        │
        ▼
   POST /generations/{id}  → BackgroundTask
        │
   ┌────┴────────────────────────────────────────────────┐
   │  PIPELINE IA (Colab GPU T4)                         │
   │                                                      │
   │  1. rembg          → détourage fond transparent     │
   │  2. CATEGORY_MAP   → catégorie produit (6 types)    │
   │  3. SDXL Inpaint   → fond premium généré            │
   │  4. Pillow         → composition + ombre portée     │
   │  5. Qwen2.5/Ollama → texte marketing JSON           │
   │  6. Pillow         → affiche finale (3 templates)   │
   │  7. Resize         → Instagram/Facebook/LinkedIn    │
   │  8. Cloudinary     → upload URLs                    │
   └────────────────────────────────────────────────────┘
        │
        ▼
   GET /generations/{id}   → polling statut
        │
        ▼
   GET /generations/{id}/assets  → 3 URLs finales
```

---

## Prérequis

| Composant | Version | Notes |
|-----------|---------|-------|
| Python | 3.10+ | Backend + pipeline |
| Node.js | 18+ | Frontend |
| PostgreSQL | 14+ | Base de données locale |
| Google Colab | — | Exécution pipeline IA (GPU T4 requis) |
| Cloudinary | — | Compte gratuit (25 GB) |
| ngrok | — | Compte gratuit pour tunnel |

---

## Lancement Backend (local dev)

```powershell
# 1. Créer et activer l'environnement virtuel
cd D:\Stage_1_ouvrier\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Copier et configurer .env
Copy-Item .env.example .env
# Éditez .env avec votre PostgreSQL et Cloudinary

# 4. Créer la base de données PostgreSQL
# Via psql:
# CREATE DATABASE cosmetique_ai;

# 5. Appliquer les migrations
alembic upgrade head

# 6. (Optionnel) Insérer des données de test
python seed.py

# 7. Lancer le serveur
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

→ API disponible sur http://localhost:8000  
→ Documentation Swagger : http://localhost:8000/docs

---

## Lancement Frontend

```powershell
cd D:\Stage_1_ouvrier\frontend

# 1. Installer les dépendances
npm install

# 2. Configurer l'URL de l'API
Copy-Item .env.example .env.local
# Mettez VITE_API_URL=http://localhost:8000 pour le dev local

# 3. Démarrer le serveur de développement
npm run dev
```

→ Frontend sur http://localhost:5173

---

## Exécution sur Colab

1. **Uploader le code** sur Google Drive :
   - Copier `D:\Stage_1_ouvrier\backend\` dans `Google Drive/Stage_1_ouvrier/backend/`

2. **Ouvrir le notebook** : `notebook/pipeline_ia_cosmetique.ipynb`
   - Importer dans Google Colab depuis Drive

3. **Configurer** (Cellule 3) :
   - Renseigner `DATABASE_URL` avec ngrok TCP de votre PostgreSQL local
   - Renseigner `CLOUDINARY_*` et `NGROK_TOKEN`

4. **Exposer PostgreSQL local** via ngrok TCP :
   ```powershell
   # Sur Windows — terminal séparé
   ngrok tcp 5432
   # → donne: tcp://0.tcp.ngrok.io:XXXXX
   # Utilisez cette URL dans DATABASE_URL du notebook
   ```

5. **Exécuter les cellules dans l'ordre** (1 → 11)

6. **Copier l'URL ngrok** (Cellule 10) dans le frontend `.env.local` :
   ```
   VITE_API_URL=https://xxxx.ngrok-free.app
   ```

---

## Variables d'environnement

### Backend (`backend/.env`)

| Variable | Description | Exemple |
|----------|-------------|---------|
| `DATABASE_URL` | URL PostgreSQL | `postgresql://postgres:your_password@localhost:5432/cosmetique_ai` |
| `SECRET_KEY` | Clé JWT (32+ chars) | `openssl rand -hex 32` |
| `ALGORITHM` | Algorithme JWT | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Durée token | `30` |
| `CLOUDINARY_CLOUD_NAME` | Nom Cloudinary | `my-cloud` |
| `CLOUDINARY_API_KEY` | API Key Cloudinary | `123456789` |
| `CLOUDINARY_API_SECRET` | API Secret | `abc...` |
| `CORS_ORIGINS` | Origines CORS | `http://localhost:5173` |
| `MAX_UPLOAD_SIZE_MB` | Taille max upload | `10` |

### Frontend (`frontend/.env.local`)

| Variable | Description |
|----------|-------------|
| `VITE_API_URL` | URL de l'API FastAPI |

---

## API Reference

### Auth
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/auth/register` | Créer un compte | ❌ |
| POST | `/auth/login` | Connexion → JWT | ❌ |

### Products
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/products` | Upload image + métadonnées | ✅ |
| GET | `/products` | Liste des produits | ✅ |
| GET | `/products/{id}` | Détail produit | ✅ |

### Generations
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/generations/{product_id}` | Lancer pipeline IA | ✅ |
| GET | `/generations/{id}` | Statut + résultat | ✅ |
| GET | `/generations/{id}/assets` | 3 assets finaux | ✅ |
| POST | `/generations/{id}/regenerate-text` | Relancer texte | ✅ |
| POST | `/generations/{id}/regenerate-decor` | Relancer décor | ✅ |

---

## Sécurité

| Mesure | Détail |
|--------|--------|
| **Mots de passe** | bcrypt cost 12 |
| **JWT** | HS256, expiry 30 min |
| **Timing attack** | `verify_password` appelé même si user inexistant |
| **User enumeration** | Message d'erreur générique sur register/login |
| **File upload** | Validation magic bytes + MIME type + taille max |
| **Authorization** | Vérification `user_id` sur chaque ressource |
| **Rate limiting** | 10 req/min register, 20 req/min login, 200 req/min global |
| **CORS** | Origines whitelist uniquement |
| **Headers** | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection |
| **SQL injection** | SQLAlchemy ORM (requêtes paramétrées) |

---

## Structure du projet

```
Stage_1_ouvrier/
├── backend/                # FastAPI
│   ├── app/
│   │   ├── core/          # config.py, security.py
│   │   ├── routers/       # auth.py, products.py, generations.py
│   │   ├── services/      # pipeline.py, storage.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── dependencies.py
│   │   └── main.py
│   ├── alembic/           # migrations
│   ├── seed.py
│   └── requirements.txt
│
├── frontend/               # React.js (Vite)
│   └── src/
│       ├── pages/         # Login, Register, Dashboard, Upload, Generation, Result
│       ├── components/    # Navbar, PosterPreview, StepLoader, AssetCard
│       └── api/           # client.js (axios)
│
├── notebook/               # Google Colab
│   └── pipeline_ia_cosmetique.ipynb
│
└── docker/                 # (BLOC 6 — plus tard)
```
