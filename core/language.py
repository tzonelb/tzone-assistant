class LanguageManager:

    def __init__(self):
        self.languages = {
            "en": "English",
            "ar": "العربية"
        }

    def is_supported(self, language):
        return language in self.languages

    def get_name(self, language):
        return self.languages.get(language)


language = LanguageManager()