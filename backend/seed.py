"""
Seed script — inserts a test user + test product into the DB.

Usage:
  cd backend
  python seed.py

Requires:
  - .env configured with DATABASE_URL
  - Tables already created via: alembic upgrade head
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.security import hash_password
from app.database import SessionLocal, engine
from app.models import Base, Generation, GenerationStatus, Product, User

TEST_EMAIL = "demo@cosmetique-ai.com"
TEST_PASSWORD = "Demo1234!"

def seed():
    db = SessionLocal()
    try:
        from sqlalchemy import select

        existing_user = db.scalar(select(User).where(User.email == TEST_EMAIL))
        if existing_user:
            print(f"OK: User already exists: {TEST_EMAIL}")
            user = existing_user
        else:
            user = User(email=TEST_EMAIL, hashed_password=hash_password(TEST_PASSWORD))
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"OK: Created user: {TEST_EMAIL} / {TEST_PASSWORD}")

        existing_products = db.scalars(
            select(Product).where(Product.user_id == user.id)
        ).all()
        for p in existing_products:
            db.delete(p)
        db.commit()

        product = Product(
            user_id=user.id,
            name="Crème Hydratante Éclat",
            brand="Lumière Paris",
            category="Soin Visage",
            original_image_url="https://images.unsplash.com/photo-1620916566398-39f1143ab7be?auto=format&fit=crop&q=80&w=800",
        )
        db.add(product)
        db.commit()
        db.refresh(product)
        print(f"OK: Created product: {product.name} (id={product.id})")

        generation = Generation(
            product_id=product.id,
            status=GenerationStatus.PENDING,
            tone="luxe",
            template="classic",
        )
        db.add(generation)
        db.commit()
        db.refresh(generation)
        print(f"OK: Created generation (id={generation.id}, status=pending)")

        print("\n[OK] Seed complete!")
        print(f"   Login: {TEST_EMAIL} / {TEST_PASSWORD}")
        print(f"   Product ID: {product.id}")
        print(f"   Generation ID: {generation.id}")

    except Exception as e:
        db.rollback()
        print(f"ERROR: Seed failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()
