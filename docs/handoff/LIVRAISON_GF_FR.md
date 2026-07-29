# Livraison Cosmetique AI

Document de remise pour le stage / ITGATE.  
Date : 2026-07-29

## Qu’est-ce que c’est ?

**Cosmetique AI** est un studio privé de campagnes publicitaires cosmétiques :

- site web en **français** (React) ;
- une campagne **FR ou EN** par génération ;
- exports exacts Instagram `1080×1080`, Facebook `1200×630`, LinkedIn `1200×627` ;
- le produit photographié est **conservé** ; seule l’arrière-plan est généré ;
- le texte marketing ne peut utiliser que les faits fournis (pas d’invention de claims).

## Architecture (simple)

```text
Navigateur  →  Nginx + FastAPI  →  PostgreSQL + worker
                                      ↓
                         Colab T4 (tunnel Cloudflare)
                                      ↓
                              ZIP validé (8 fichiers)
```

Le navigateur ne voit jamais l’URL Colab ni le token GPU.

## Ce qui est déjà fait (preuves locales)

Voir `verification-evidence.md` / `reports/release-gates.md` :

- tests `ai_core` : 67 OK ;
- tests backend : 119 OK (+1 skip) ;
- frontend : lint, 51 tests, build, 15 E2E Playwright OK ;
- notebook Colab validé (sans sorties sauvées) ;
- stack Docker locale : Postgres, migrations, API, worker, frontend **healthy** ;
- modèles Hugging Face pinés et confirmés sur le Hub.

## Ce que tu dois faire immédiatement

1. Suivre **`COLAB_IMMEDIATE_FR.md`** (runtime T4 → Run all → coller URL + token dans `docker/.env`).
2. Lancer une vraie génération de bout en bout.
3. (Évaluation) protocole privé 24 images : `docs/EVALUATION.md` / `AI_EVALUATION_PROTOCOL.md` — uniquement avec images autorisées.

## Démarrer Docker sans Colab (UI seulement)

```powershell
Copy-Item .\docker\.env.example .\docker\.env
# Remplir tous les secrets ; laisser un placeholder Colab tant que le notebook n’a pas tourné
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up --build -d
```

Sans Colab prêt, le site démarre mais les générations GPU échoueront explicitement (comportement voulu).

## Dépôt GitHub

Repo privé : https://github.com/jessem8/cosmetique-ai  
Branche : `codex/implementation`

## Documents utiles

| Fichier | Rôle |
|---|---|
| `COLAB_IMMEDIATE_FR.md` | Carte 1 page pour lancer Colab maintenant |
| `docs/OPERATIONS.md` | Ops Docker + recovery runtime |
| `AI_EXECUTION_GUIDE.md` | Contrat notebook / API Colab |
| `IMPLEMENTATION_CONTRACT.md` (workspace parent) / docs API | Contrats d’intégration |
| `SECURITY.md` | Limites de confiance |
| `reports/release-gates.md` | Checklist preuves |

## Non inclus / à ne pas inventer

- Pas de succès simulé, pas de barre de progression fictive.
- Pas d’évaluation privée exécutée dans cette remise (données hors dépôt).
- Scan « Codex Security » volontairement écarté ; audits locaux documentés à la place.
