import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)


def get_env(name: str, default: str = "") -> str:
    import os
    return os.getenv(name, default)


def main():
    api_key = get_env("OPENAI_API_KEY")
    model = get_env("OPENAI_MODEL", "gpt-4.1-mini")
    api_url = get_env("OPENAI_API_URL", "https://api.openai.com/v1/responses")

    if not api_key:
        print("❌ OPENAI_API_KEY is missing in .env")
        sys.exit(1)

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": "You are a health check assistant. Return JSON only."
            },
            {
                "role": "user",
                "content": "Return this exact JSON: {\"status\":\"ok\",\"message\":\"AI connected\"}"
            }
        ],
        "text": {
            "format": {
                "type": "json_object"
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(api_url, headers=headers, json=payload)

        print("HTTP STATUS:", response.status_code)

        if response.status_code >= 400:
            print("❌ API ERROR:")
            print(response.text)
            sys.exit(1)

        data = response.json()

        output_text = data.get("output_text")

        if not output_text:
            for item in data.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") in ["output_text", "text"]:
                        output_text = content.get("text")
                        break

        print("RAW OUTPUT:")
        print(output_text)

        parsed = json.loads(output_text)

        if parsed.get("status") == "ok":
            print("✅ AI CONNECTED SUCCESSFULLY")
            return

        print("⚠️ AI replied, but unexpected JSON:")
        print(parsed)

    except Exception as e:
        print("❌ TEST FAILED:")
        print(type(e).__name__)
        print(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()