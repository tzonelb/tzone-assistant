import json
from pathlib import Path


class BusinessModules:
    CONFIG_FILE = Path("config") / "business_modules.json"

    def load_modules(self) -> list[dict]:
        if not self.CONFIG_FILE.exists():
            return []

        with open(self.CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data.get("modules", [])

    def enabled_modules(self) -> list[dict]:
        return [m for m in self.load_modules() if m.get("enabled", True)]

    def buttons(self, language: str) -> list[str]:
        key = "button_ar" if language == "ar" else "button_en"
        return [m.get(key) for m in self.enabled_modules() if m.get(key)]

    def overview_text(self, language: str) -> str:
        modules = self.enabled_modules()

        if language == "en":
            names = [m.get("name_en") for m in modules if m.get("name_en")]
            return "Available sections: " + ", ".join(names) + "."

        names = [m.get("name_ar") for m in modules if m.get("name_ar")]
        return "الأقسام المتاحة: " + "، ".join(names) + "."

    def get_module_by_button(self, button_text: str, language: str) -> dict | None:
        key = "button_ar" if language == "ar" else "button_en"

        for module in self.enabled_modules():
            if button_text == module.get(key):
                return module

        return None


business_modules = BusinessModules()