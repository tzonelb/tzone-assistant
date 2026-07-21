class IntentDetector:
    INTENT_STATE_MAP = {
        "iptv_channels_problem": "iptv_solution_channels",
        "iptv_login_problem": "iptv_solution_login",
        "iptv_buffering_problem": "iptv_solution_buffering",
        "iptv_vod_problem": "iptv_solution_vod",
        "iptv_sound_problem": "iptv_solution_sound",
        "iptv_faq": "iptv_faq",
    }

    KEYWORDS = {
        "iptv_channels_problem": [
            "channel", "channels", "no channel", "not working",
            "قنوات", "القنوات", "القناة", "ما في قنوات", "لا تعمل",
            "مش شغالة", "ما عم تشتغل", "ما يشتغل", "مش شغال"
        ],
        "iptv_login_problem": [
            "login", "username", "password", "account",
            "دخول", "تسجيل", "يوزر", "باسورد", "كلمة السر",
            "الحساب", "ما عم يفوت", "ما بيفتح", "ما عم يفتح"
        ],
        "iptv_buffering_problem": [
            "buffer", "buffering", "lag", "slow", "freezing",
            "تقطيع", "يقطع", "بطيء", "تعليق", "يعلق", "بيعلق"
        ],
        "iptv_vod_problem": [
            "vod", "movie", "movies", "series",
            "افلام", "أفلام", "مسلسلات", "فيلم", "مسلسل", "المكتبة"
        ],
        "iptv_sound_problem": [
            "sound", "audio", "voice", "no sound",
            "صوت", "الصوت", "ما في صوت", "بدون صوت"
        ],
        "iptv_faq": [
            "faq", "question", "help",
            "سؤال", "اسئلة", "أسئلة", "مساعدة", "ساعدني"
        ],
    }

    def detect(self, message: str) -> str | None:
        if not message:
            return None

        normalized = message.lower().strip()

        for intent, keywords in self.KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in normalized:
                    return intent

        return None

    def get_state_for_intent(self, intent: str | None) -> str | None:
        if not intent:
            return None

        return self.INTENT_STATE_MAP.get(intent)


intent_detector = IntentDetector()