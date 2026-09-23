import os
import sys
from google import genai
from app.config import settings


def verify_gemini_connection():
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")

    if not api_key or api_key == "mock_key_for_now":
        print("❌ Error: GEMINI_API_KEY is missing or set to the mock placeholder in your .env file!")
        print("Please update GEMINI_API_KEY in .env with your actual key from Google AI Studio.")
        sys.exit(1)

    print("🔍 Testing connection to Google AI Studio Gemini API...")

    try:
        # Initialize client with the google-genai SDK
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents="Confirm API key readiness for CV screening system. Respond with JSON: {\"status\": \"ready\"}",
        )

        print("\n✅ Connection Successful!")
        print(f"🤖 Gemini Output: {response.text.strip()}\n")

    except Exception as e:
        print(f"\n❌ Gemini API Key Verification Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    verify_gemini_connection()