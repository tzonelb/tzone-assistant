import json
from pathlib import Path


class KnowledgeManager:
    BASE_FILE = Path("config") / "knowledge_base.json"
    TRAINING_FILE = Path("config") / "training_knowledge.json"

    def load_json_items(self, path: Path) -> list[dict]:
        if not path.exists():
            return []

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data.get("items", [])

    def load_items(self) -> list[dict]:
        items = []
        items.extend(self.load_json_items(self.BASE_FILE))
        items.extend(self.load_json_items(self.TRAINING_FILE))
        return items

    def list_for_ai(self, department: str | None = None) -> list[dict]:
        items = self.load_items()

        if not department:
            return items

        return [
            item for item in items
            if item.get("department") in [department, "information"]
        ]


knowledge_manager = KnowledgeManager()