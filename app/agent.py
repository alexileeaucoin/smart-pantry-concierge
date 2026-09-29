# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-02-a6620e5c2935"
GCS_BUCKET_NAME = "smart-pantry-assets-62968065"

# Load Agent Engine resource name from deployment_metadata.json if available
agent_engine_resource_name = None
memory_bank_id = "8288925691710275584"
metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(metadata_path):
    try:
        with open(metadata_path, "r") as f:
            meta = json.load(f)
            agent_engine_resource_name = meta.get("remote_agent_runtime_id")
            if agent_engine_resource_name:
                memory_bank_id = agent_engine_resource_name.split("/")[-1]
    except Exception:
        pass

# Ensure Agent Platform environment variable is set for memory service auto-config
os.environ["GOOGLE_CLOUD_AGENT_ENGINE_ID"] = memory_bank_id


def memory_bank_service_builder():
    """Builds VertexAiMemoryBankService pointing to the Agent Engine Memory Bank instance."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=memory_bank_id,
    )


# Initialize Sandbox Code Executor
sandbox_code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=agent_engine_resource_name
)

# Initialize Firestore client with hardcoded project ID string as required for Agent Platform
db = firestore.Client(project=FIRESTORE_PROJECT_ID)

# Nutritional reference table per standard ingredient serving
NUTRITION_DATABASE = {
    "spaghetti pasta": {"calories": 350, "protein_g": 12.0, "carbs_g": 70.0, "fat_g": 2.0},
    "extra virgin olive oil": {"calories": 120, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 14.0},
    "fresh garlic": {"calories": 10, "protein_g": 0.5, "carbs_g": 2.0, "fat_g": 0.0},
    "diced canned tomatoes": {"calories": 50, "protein_g": 2.0, "carbs_g": 10.0, "fat_g": 0.0},
    "parmesan cheese": {"calories": 110, "protein_g": 10.0, "carbs_g": 1.0, "fat_g": 7.0},
}

# A2UI Schema Manager setup for Version 0.8
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Smart Pantry Concierge, a helpful AI assistant that manages pantry inventory, "
        "calculates recipe nutrition, searches online recipes, generates recipe images, finds nearby grocery stores, "
        "and can safely execute Python code in a sandbox environment."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects.\n\n"
        "CRITICAL DIETARY SAFETY & ALLERGY REQUIREMENT:\n"
        "You MUST remember, track, and strictly enforce all user dietary allergies, food intolerances, and dietary restrictions "
        "(e.g., peanuts, tree nuts, shellfish, gluten, dairy, eggs, soy, sesame, etc.) stated across conversations.\n"
        "1. Whenever the user mentions any allergy or food restriction, acknowledge it and rely on Memory Bank to store it permanently.\n"
        "2. Before recommending any recipe, checking pantry items, or generating dish ideas, inspect preloaded memories for user allergies.\n"
        "3. NEVER suggest or include ingredients that contain any of the user's saved allergens."
    ),
    include_schema=True,
    include_examples=True,
)


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends session conversation to Memory Bank after each agent turn to extract facts and preferences."""
    await callback_context.add_session_to_memory()
    return None


def generate_recipe_image(prompt: str, tool_context: ToolContext = None) -> str:
    """Generates an image of a food or recipe using gemini-3.1-flash-lite-image model, saves it as an artifact, and uploads it to public GCS.

    Args:
        prompt: Description of the food or recipe image to generate (e.g. 'A delicious plate of Spaghetti Aglio e Olio').
        tool_context: ADK ToolContext automatically injected for managing artifacts.

    Returns:
        The public HTTPS URL of the generated image stored in Cloud Storage.
    """
    try:
        genai_client = genai.Client(
            vertexai=True, location="global", project=FIRESTORE_PROJECT_ID
        )
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        image_part = None
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_part = part
                    break

        if not image_part or not image_part.inline_data:
            return "Failed to generate image: No image data returned."

        image_bytes = image_part.inline_data.data
        mime_type = image_part.inline_data.mime_type or "image/jpeg"
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"recipe_{timestamp}.jpg"

        # 1. Save as artifact for Playground
        if tool_context:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Directly upload image bytes to public GCS bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob_name = f"recipe_images/{filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{blob_name}"
        return f"Successfully generated image! Public URL: {public_url}"
    except Exception as e:
        return f"Error generating image: {str(e)}"


def generate_recipe_video(prompt: str, tool_context: ToolContext = None) -> str:
    """Generates a short video of a food item or recipe using gemini-omni-flash-preview model in the global region, saves it as an artifact, and uploads it to public GCS.

    Args:
        prompt: Description of the food video to generate (e.g. 'A short video of sizzling garlic in olive oil').
        tool_context: ADK ToolContext automatically injected for managing artifacts.

    Returns:
        The public HTTPS URL of the generated video stored in Cloud Storage.
    """
    try:
        genai_client = genai.Client(
            vertexai=True, location="global", project=FIRESTORE_PROJECT_ID
        )
        response = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=prompt,
            response_modalities=["video"],
        )

        video_bytes = None
        mime_type = "video/mp4"

        if hasattr(response, "outputs"):
            for out in response.outputs:
                if getattr(out, "data", None):
                    video_bytes = out.data
                    if getattr(out, "mime_type", None):
                        mime_type = out.mime_type
                    break

        if not video_bytes and hasattr(response, "candidates"):
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.data:
                        video_bytes = part.inline_data.data
                        if part.inline_data.mime_type:
                            mime_type = part.inline_data.mime_type
                        break

        if not video_bytes:
            return "Failed to generate video: No video bytes returned."

        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"recipe_video_{timestamp}.mp4"

        # 1. Save as artifact for Playground
        if tool_context:
            artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
            tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Directly upload video bytes to public GCS bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob_name = f"recipe_videos/{filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{blob_name}"
        return f"Successfully generated video! Public URL: {public_url}"
    except Exception as e:
        return f"Error generating video: {str(e)}"


def list_pantry_items() -> str:
    """Retrieves all current pantry items stored in the Firestore database.

    Returns:
        A formatted list of pantry items including name, quantity, unit, and category.
    """
    try:
        docs = db.collection("pantry_items").stream()
        items = []
        for doc in docs:
            data = doc.to_dict()
            items.append(
                f"- {data.get('name')}: {data.get('quantity')} {data.get('unit')} ({data.get('category')})"
            )
        if not items:
            return "Your pantry is currently empty."
        return "Current Pantry Inventory:\n" + "\n".join(items)
    except Exception as e:
        return f"Error retrieving pantry items: {str(e)}"


def update_pantry_item(
    item_name: str, quantity: float, unit: str, category: str = "General"
) -> str:
    """Adds a new item or updates an existing item's quantity in the Firestore pantry database.

    Args:
        item_name: The name of the food or pantry item (e.g. 'Garlic', 'Olive Oil').
        quantity: The quantity amount (e.g. 2.0, 500).
        unit: The measurement unit (e.g. 'cloves', 'grams', 'bottles').
        category: The item category (e.g. 'Produce', 'Pantry Staples', 'Dairy & Eggs').

    Returns:
        Confirmation message of the update.
    """
    try:
        doc_id = item_name.lower().replace(" ", "-")
        doc_ref = db.collection("pantry_items").document(doc_id)
        data = {
            "item_id": doc_id,
            "name": item_name,
            "quantity": float(quantity),
            "unit": unit,
            "category": category,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        doc_ref.set(data)
        return f"Successfully updated '{item_name}' in pantry with quantity {quantity} {unit}."
    except Exception as e:
        return f"Error updating pantry item: {str(e)}"


def list_recipes() -> str:
    """Retrieves available recipe recommendations stored in the Firestore database.

    Returns:
        A formatted list of available recipes with titles, ingredients, and instructions.
    """
    try:
        docs = db.collection("recipes").stream()
        recipes = []
        for doc in docs:
            data = doc.to_dict()
            ingredients = ", ".join(data.get("ingredients", []))
            recipes.append(
                f"📖 **{data.get('title')}** ({data.get('prep_time_mins')} mins)\n"
                f"   Ingredients: {ingredients}\n"
                f"   Instructions: {data.get('instructions')}"
            )
        if not recipes:
            return "No recipes found in the database."
        return "\n\n".join(recipes)
    except Exception as e:
        return f"Error retrieving recipes: {str(e)}"


def calculate_recipe_nutrition(recipe_name: str, servings: int = 1) -> str:
    """Calculates estimated nutritional info (calories, protein, carbs, fat) for a recipe in Firestore.

    Args:
        recipe_name: Name of the recipe (e.g. 'Classic Garlic Tomato Pasta' or 'Spaghetti Aglio e Olio').
        servings: Number of servings to calculate nutrition for (default is 1).

    Returns:
        A formatted string with total calories and macronutrient breakdown.
    """
    try:
        doc_id = recipe_name.lower().replace(" ", "-")
        doc = db.collection("recipes").document(doc_id).get()
        if not doc.exists:
            docs = db.collection("recipes").stream()
            matching_doc = None
            for d in docs:
                data = d.to_dict()
                if recipe_name.lower() in data.get("title", "").lower():
                    matching_doc = data
                    break
            if not matching_doc:
                return f"Recipe '{recipe_name}' was not found in the database."
            recipe = matching_doc
        else:
            recipe = doc.to_dict()

        ingredients = recipe.get("ingredients", [])
        total_cal = 0
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0

        for ing in ingredients:
            info = NUTRITION_DATABASE.get(
                ing.lower(),
                {"calories": 100, "protein_g": 3.0, "carbs_g": 15.0, "fat_g": 3.0},
            )
            total_cal += info["calories"]
            total_protein += info["protein_g"]
            total_carbs += info["carbs_g"]
            total_fat += info["fat_g"]

        total_cal = int(total_cal * servings)
        total_protein *= servings
        total_carbs *= servings
        total_fat *= servings

        return (
            f"Nutritional Estimate for '{recipe['title']}' ({servings} serving(s)):\n"
            f"- Calories: {total_cal} kcal\n"
            f"- Protein: {total_protein:.1f} g\n"
            f"- Carbs: {total_carbs:.1f} g\n"
            f"- Fat: {total_fat:.1f} g"
        )
    except Exception as e:
        return f"Error calculating nutrition: {str(e)}"


def search_online_recipes(query: str) -> str:
    """Searches the public TheMealDB API for real recipe ideas matching a dish or ingredient keyword.

    Args:
        query: Ingredient or dish name to search online (e.g. 'pasta', 'chicken', 'garlic').

    Returns:
        A summary of top matching recipes found from the online database.
    """
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={encoded_query}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "SmartPantryAgent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        meals = data.get("meals")
        if not meals:
            return f"No online recipes found matching query '{query}'."

        results = []
        for meal in meals[:3]:
            title = meal.get("strMeal")
            category = meal.get("strCategory", "General")
            area = meal.get("strArea", "International")
            thumb = meal.get("strMealThumb", "")
            instructions = meal.get("strInstructions", "")[:150] + "..."
            results.append(
                f"🍳 **{title}** ({area} {category})\n"
                f"   Instructions: {instructions}\n"
                f"   Image: {thumb}"
            )

        return f"Online Recipes matching '{query}':\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error fetching online recipes: {str(e)}"


def geocode_address(address: str) -> str:
    """Converts a street address, city, or location name into geographic coordinates (latitude and longitude).

    Args:
        address: The address or place name to geocode (e.g., 'San Francisco, CA' or '1600 Amphitheatre Pkwy').

    Returns:
        A string containing the formatted address and latitude/longitude coordinates.
    """
    try:
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            return "Error: GOOGLE_MAPS_API_KEY is missing from environment variables."

        encoded_address = urllib.parse.quote(address)
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = data.get("results")
        if not results:
            return f"No coordinates found for address '{address}'."

        first = results[0]
        formatted_address = first.get("formatted_address")
        location = first.get("geometry", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")

        return (
            f"Geocoding Result for '{address}':\n"
            f"- Address: {formatted_address}\n"
            f"- Latitude: {lat}\n"
            f"- Longitude: {lng}"
        )
    except Exception as e:
        return f"Error geocoding address: {str(e)}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "supermarket",
    radius_meters: float = 5000.0,
) -> str:
    """Finds nearby places (e.g. supermarket, grocery_store, bakery) around a given latitude and longitude using Places API (New).

    Args:
        latitude: Latitude coordinate (e.g. 37.7749).
        longitude: Longitude coordinate (e.g. -122.4194).
        place_type: Type of place to search for (e.g. 'supermarket', 'grocery_store', 'bakery', 'restaurant').
        radius_meters: Search radius in meters (default 5000 meters).

    Returns:
        A list of matching nearby places with name, formatted address, and location coordinates.
    """
    try:
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            return "Error: GOOGLE_MAPS_API_KEY is missing from environment variables."

        url = "https://places.googleapis.com/v1/places:searchNearby"
        payload = json.dumps(
            {
                "includedTypes": [place_type],
                "maxResultCount": 5,
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": latitude, "longitude": longitude},
                        "radius": radius_meters,
                    }
                },
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        }

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        places = data.get("places", [])
        if not places:
            return f"No nearby places of type '{place_type}' found."

        results = []
        for p in places:
            display_name = p.get("displayName", {}).get("text", "Unknown Name")
            address = p.get("formattedAddress", "N/A")
            loc = p.get("location", {})
            lat = loc.get("latitude")
            lng = loc.get("longitude")
            results.append(
                f"📍 **{display_name}**\n"
                f"   Address: {address}\n"
                f"   Location: ({lat}, {lng})"
            )

        return f"Nearby '{place_type}' locations:\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error finding nearby places: {str(e)}"


def get_weather(query: str) -> str:
    """Simulates getting weather information for a location.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return (
        f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"
    )


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=sandbox_code_executor,
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        list_pantry_items,
        update_pantry_item,
        list_recipes,
        calculate_recipe_nutrition,
        search_online_recipes,
        generate_recipe_image,
        generate_recipe_video,
        geocode_address,
        find_nearby_places,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
