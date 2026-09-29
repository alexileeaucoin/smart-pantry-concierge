"""Seed script for Smart Pantry Concierge Firestore database."""

import google.auth
from google.cloud import firestore

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-02-a6620e5c2935"

# Hardcode project ID string as requested:
creds, _ = google.auth.default()
db = firestore.Client(project=FIRESTORE_PROJECT_ID, credentials=creds)

INITIAL_PANTRY_ITEMS = [
    {
        "item_id": "olive-oil",
        "name": "Extra Virgin Olive Oil",
        "quantity": 1.5,
        "unit": "bottles",
        "category": "Pantry Staples",
        "updated_at": "2026-09-29T16:00:00Z",
    },
    {
        "item_id": "spaghetti",
        "name": "Spaghetti Pasta",
        "quantity": 2.0,
        "unit": "boxes",
        "category": "Grains & Pasta",
        "updated_at": "2026-09-29T16:00:00Z",
    },
    {
        "item_id": "garlic",
        "name": "Fresh Garlic",
        "quantity": 6.0,
        "unit": "cloves",
        "category": "Produce",
        "updated_at": "2026-09-29T16:00:00Z",
    },
    {
        "item_id": "canned-tomatoes",
        "name": "Diced Canned Tomatoes",
        "quantity": 3.0,
        "unit": "cans",
        "category": "Canned Goods",
        "updated_at": "2026-09-29T16:00:00Z",
    },
    {
        "item_id": "parmesan",
        "name": "Parmesan Cheese",
        "quantity": 200.0,
        "unit": "grams",
        "category": "Dairy & Eggs",
        "updated_at": "2026-09-29T16:00:00Z",
    },
]

INITIAL_RECIPES = [
    {
        "recipe_id": "garlic-tomato-pasta",
        "title": "Classic Garlic Tomato Pasta",
        "ingredients": ["Spaghetti Pasta", "Extra Virgin Olive Oil", "Fresh Garlic", "Diced Canned Tomatoes", "Parmesan Cheese"],
        "prep_time_mins": 20,
        "instructions": "1. Boil spaghetti in salted water. 2. Sauté minced garlic in olive oil. 3. Add canned tomatoes and simmer for 10 mins. 4. Toss with pasta and top with grated parmesan.",
    },
    {
        "recipe_id": "garlic-oil-pasta",
        "title": "Spaghetti Aglio e Olio",
        "ingredients": ["Spaghetti Pasta", "Extra Virgin Olive Oil", "Fresh Garlic", "Parmesan Cheese"],
        "prep_time_mins": 15,
        "instructions": "1. Boil spaghetti until al dente. 2. Gently heat sliced garlic in olive oil until golden. 3. Toss pasta with garlic oil and parmesan cheese.",
    },
]


def seed_database():
    print(f"Seeding Firestore project: {FIRESTORE_PROJECT_ID}")

    pantry_ref = db.collection("pantry_items")
    for item in INITIAL_PANTRY_ITEMS:
        pantry_ref.document(item["item_id"]).set(item)
        print(f"  Added pantry item: {item['name']}")

    recipes_ref = db.collection("recipes")
    for recipe in INITIAL_RECIPES:
        recipes_ref.document(recipe["recipe_id"]).set(recipe)
        print(f"  Added recipe: {recipe['title']}")

    print("✅ Firestore seeding completed successfully!")


if __name__ == "__main__":
    seed_database()
