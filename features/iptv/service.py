from core.response import Response


class IPTVService:

    def start(self):
        return Response(
            text="📺 IPTV Service",
            buttons=[
                "🛠️ Support",
                "💳 Sales & Renewals",
                "ℹ️ Information",
                "⬅️ Back",
                "🏠 Main Menu"
            ]
        )


iptv = IPTVService()