import json


class MenuManager:

    def __init__(self):
        with open("data/menus.json", "r", encoding="utf-8") as file:
            self.data = json.load(file)

    def get_main_menu(self, language):
        return self.data["main_menu"][language]


menu = MenuManager()