import json
from pathlib import Path


class ContentLoader:
    def __init__(self):
        self.content = {}
        self.load_all_content()

    def load_all_content(self):
        features_path = Path("features")

        for content_file in features_path.glob("*/content.json"):
            feature_name = content_file.parent.name

            with open(content_file, "r", encoding="utf-8") as file:
                self.content[feature_name] = json.load(file)

    def get_faq_text(self, feature, language):
        faqs = self.content.get(feature, {}).get("faqs", [])

        if not faqs:
            return "No FAQs available."

        lines = ["❓ FAQ\n"]

        for item in faqs:
            question = item.get(f"question_{language}", "")
            answer = item.get(f"answer_{language}", "")

            lines.append(f"• {question}\n{answer}\n")

        return "\n".join(lines)

    def get_solution_text(self, feature, solution_id, language):
        solutions = self.content.get(feature, {}).get("solutions", [])

        for item in solutions:
            if item.get("id") == solution_id:
                title = item.get(f"title_{language}", "")
                answer = item.get(f"answer_{language}", "")
                return f"{title}\n\n{answer}"

        return None


content_loader = ContentLoader()