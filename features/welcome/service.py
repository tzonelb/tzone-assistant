from core.response import Response


class WelcomeService:

    def start(self):
        return Response(
            text="👋 Welcome to T-ZONE Assistant\n\n🌍 Please choose your language\n\nاختر لغتك",
            buttons=[
                "🇬🇧 English",
                "🇸🇦 العربية"
            ]
        )


welcome = WelcomeService()