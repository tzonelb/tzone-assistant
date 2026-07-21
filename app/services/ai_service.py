async def generate_ai_reply(platform: str, user_id: str, message: str) -> str:
    """
    Temporary AI reply for Release 2.3.
    Later this function will connect to the real AI assistant logic.
    """

    if not message:
        return "أهلاً وسهلاً بك في T-ZONE 💙 كيف فينا نساعدك؟"

    return f"أهلاً وسهلاً بك في T-ZONE 💙 تم استلام رسالتك: {message}"