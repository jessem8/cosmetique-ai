from __future__ import annotations

import os


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://cosmetique_test:cosmetique_test@localhost:55432/cosmetique_test",
)
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-with-at-least-32-bytes")
os.environ.setdefault("ARTIFACT_ROOT", "test-artifacts")
os.environ.setdefault("COLAB_AI_URL", "https://ai-runtime.test")
os.environ.setdefault("AI_SERVICE_TOKEN", "test-only-ai-token-with-at-least-32-bytes")

