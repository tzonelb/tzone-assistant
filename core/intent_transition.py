class IntentTransitionManager:
    GENERAL_CHANNELS = [
        "messenger",
        "whatsapp",
        "instagram",
        "website_chat",
    ]

    DEPARTMENT_KEYWORDS = {
        "sales": [
            "لابتوب", "لابتوبات", "لاب توب", "laptop", "gaming",
            "ألعاب", "العاب", "تلفون", "هواتف", "موبايل",
            "iphone", "samsung", "اكسسوار", "إكسسوار",
            "سعر", "price", "متوفر", "عندكم"
        ],
        "iptv": [
            "iptv", "اشتراك", "قنوات", "القنوات", "smarters",
            "تقطيع", "لا تعمل", "تجديد", "كود", "يوزر"
        ],
        "maintenance": [
            "تصليح", "صيانة", "شاشة", "بطارية", "فرمتة",
            "software", "hardware", "repair"
        ],
        "telecom": [
            "alfa", "touch", "تشريج", "شحن", "ushare", "u-share",
            "خط", "داتا"
        ],
        "accounting": [
            "فاتورة", "حساب", "دفع", "قبض", "رصيد",
            "invoice", "payment", "receipt"
        ],
        "information": [
            "وين", "العنوان", "location", "دوام", "ساعات",
            "رقم", "contact"
        ],
    }

    def detect_department(self, message: str) -> str | None:
        if not message:
            return None

        normalized = message.lower().strip()

        for department, keywords in self.DEPARTMENT_KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in normalized:
                    return department

        return None

    def should_switch_to_ai(
        self,
        channel: str,
        message: str,
        current_department: str | None = None,
    ) -> bool:
        if channel not in self.GENERAL_CHANNELS:
            return False

        detected_department = self.detect_department(message)

        if not detected_department:
            return False

        if not current_department:
            return True

        return detected_department != current_department


intent_transition_manager = IntentTransitionManager()