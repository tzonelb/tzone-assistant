class Navigation:

    HOME = "home"
    BACK = "back"

    def is_home(self, message):
        return message in ["🏠 Main Menu", "🏠 القائمة الرئيسية"]

    def is_back(self, message):
        return message in ["⬅️ Back", "⬅️ رجوع"]


navigation = Navigation()