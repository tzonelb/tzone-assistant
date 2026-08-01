import base64
import logging

import httpx

from config.settings import config

logger = logging.getLogger(__name__)

DESCRIBE_PROMPT = (
    "A customer sent this image in a support chat. Describe what's in it, briefly and factually, "
    "in plain language a support agent would find useful (e.g. a product photo, a screenshot of an "
    "error, a receipt, a broken device). Do not invent details you cannot actually see. Reply in Arabic "
    "unless the image contains readable English text, in which case you may reply in English."
)


class VisionService:
    """Lets the AI 'read' an image a customer sends by describing it via
    OpenAI's multimodal input, then feeding that description into the
    exact same text reply pipeline as a typed message. Reuses the
    existing OPENAI_API_KEY and Responses API endpoint."""

    def describe_image(self, image_bytes: bytes, *, mime_type: str = "image/jpeg") -> str:
        if not image_bytes:
            raise ValueError("No image to describe.")
        if not config.OPENAI_API_KEY:
            raise ValueError("AI is not configured on this server (missing OPENAI_API_KEY).")

        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{mime_type or 'image/jpeg'};base64,{encoded}"

        payload = {
            "model": config.OPENAI_VISION_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": DESCRIBE_PROMPT},
                        {"type": "input_image", "image_url": data_uri},
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=40) as client:
            response = client.post(config.OPENAI_API_URL, headers=headers, json=payload)

        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI vision error {response.status_code}: {response.text}")

        description = self._extract_output_text(response.json())
        if not description:
            raise RuntimeError("OpenAI returned an empty image description")
        return description

    def _extract_output_text(self, data: dict) -> str | None:
        if data.get("output_text"):
            return data["output_text"]
        for output_item in data.get("output", []):
            for content in output_item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    return content.get("text")
        return None


vision_service = VisionService()
