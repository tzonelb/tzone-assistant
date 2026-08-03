import logging
import traceback

from core.session import session
from core.response import Response
from core.flow_loader import flow_loader
from core.content_loader import content_loader
from core.intent_detector import intent_detector
from core.intent_transition import intent_transition_manager
from core.ai_router import ai_router
from core.automation_policy import automation_policy
from core.conversation_memory import conversation_memory
from core.knowledge_manager import knowledge_manager
from core.ai_knowledge_matcher import ai_knowledge_matcher
from core.response_policy import response_policy
from core.business_connectors import business_connectors
from core.business_modules import business_modules
from backend.services.company_settings_service import company_settings_service
from database.database import db


logger = logging.getLogger(__name__)


class Engine:
    SYSTEM_STATES = [
        "language",
        "main_menu",
        "telegram_iptv_start",
        "iptv_menu",
    ]

    BACK_BUTTONS = [
        "⬅️ Back",
        "⬅️ رجوع",
    ]

    HOME_BUTTONS = [
        "🏠 IPTV Menu",
        "🏠 قائمة IPTV",
        "🏠 Main Menu",
        "🏠 القائمة الرئيسية",
        "🏠 Back to Main Menu",
        "🏠 العودة إلى القائمة الرئيسية",
    ]

    RESET_MESSAGES = [
        "/reset",
        "reset",
        "ابدأ من جديد",
        "بداية جديدة",
        "صفر المحادثة",
        "امسح المحادثة",
    ]

    ARABIC_LANGUAGE_COMMANDS = [
        "العربية",
        "عربي",
        "arabic",
        "🇸🇦 العربية",
        "🇱🇧 العربية",
    ]

    ENGLISH_LANGUAGE_COMMANDS = [
        "english",
        "الإنجليزية",
        "انجليزي",
        "إنجليزي",
        "🇬🇧 english",
    ]

    GREETING_MESSAGES = [
        "hi",
        "hello",
        "hey",
        "مرحبا",
        "اهلا",
        "أهلا",
        "اهلين",
        "أهلين",
        "السلام عليكم",
        "سلام",
    ]

    SOLUTION_STATES = {
        "iptv_solution_login": "login",
        "iptv_solution_channels": "channels",
        "iptv_solution_buffering": "buffering",
        "iptv_solution_vod": "vod",
        "iptv_solution_sound": "sound",
    }

    ERROR_TEXT = {
        "en": "⚠️ Sorry, something went wrong. Please try again.",
        "ar": "⚠️ عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى.",
    }

    INVALID_CHOICE_TEXT = {
        "en": "⚠️ Please choose one of the available options.",
        "ar": "⚠️ الرجاء اختيار أحد الخيارات المتاحة.",
    }

    def handle(self, request):
        try:
            db.create_tables()
            user_session = session.create(request.user_id)

            if self.is_reset_message(request.message):
                session.reset(request.user_id)

                language = self.detect_language(request.message)
                session.set_language(request.user_id, language)

                return self.build_main_menu_response(language)

            explicit_language = self.get_explicit_language(request.message)

            if explicit_language:
                session.set_language(
                    request.user_id,
                    explicit_language,
                )

                return self.build_language_changed_response(
                    user_id=request.user_id,
                    language=explicit_language,
                )

            message_language = self.detect_language(request.message)

            session.set_language(
                request.user_id,
                message_language,
            )

            language = message_language

            if request.message in self.HOME_BUTTONS:
                session.go_home(
                    request.user_id,
                    request.channel,
                )

                return self.build_main_menu_response(language)

            if request.message == "start":
                return self.handle_start(
                    request,
                    language,
                )

            conversation_memory.append(
                user_session,
                "user",
                request.message,
            )

            current_state = (
                user_session.get("state")
                or "telegram_iptv_start"
            )

            current_department = user_session.get(
                "current_department"
            )

            state_data = flow_loader.get_state(
                current_state
            )

            self.log_request(
                request,
                current_state,
            )

            if self.should_ai_take_priority(request):
                ai_response = self.handle_ai(
                    request=request,
                    language=language,
                    current_state=current_state,
                    current_department=current_department,
                )

                if ai_response:
                    return ai_response

            if not state_data:
                logger.error(
                    "State not found: %s",
                    current_state,
                )

                if request.channel == "telegram":
                    session.update(
                        request.user_id,
                        "state",
                        "telegram_iptv_start",
                    )

                    return self.render(
                        "telegram_iptv_start",
                        language,
                        request.user_id,
                        request.channel,
                    )

                return self.build_main_menu_response(language)

            if request.message in self.BACK_BUTTONS:
                previous_state = session.go_back(
                    request.user_id
                )

                return self.render(
                    previous_state,
                    language,
                    request.user_id,
                    request.channel,
                )

            matched_response = self.handle_button_state(
                request,
                state_data,
                language,
            )

            if matched_response:
                return matched_response

            intent_response = self.handle_intent(
                request,
                language,
            )

            if intent_response:
                return intent_response

            if "input" in state_data:
                return self.handle_input_state(
                    request,
                    state_data,
                    language,
                )

            return self.invalid_choice(
                current_state,
                language,
                request.user_id,
                request.channel,
            )

        except Exception as error:
            traceback.print_exc()
            logger.exception("Engine error")

            return Response(
                (
                    f"ENGINE ERROR:\n\n"
                    f"{type(error).__name__}\n\n"
                    f"{str(error)}"
                ),
                [],
            )

    def is_reset_message(self, message):
        if not message:
            return False

        normalized = message.strip().lower()

        return normalized in [
            item.lower()
            for item in self.RESET_MESSAGES
        ]

    def get_explicit_language(self, message):
        if not message:
            return None

        normalized = message.strip().lower()

        if normalized in [
            item.lower()
            for item in self.ARABIC_LANGUAGE_COMMANDS
        ]:
            return "ar"

        if normalized in [
            item.lower()
            for item in self.ENGLISH_LANGUAGE_COMMANDS
        ]:
            return "en"

        return None

    def is_greeting_only(self, message):
        if not message:
            return False

        cleaned = message.strip().lower()

        for symbol in [
            "؟",
            "?",
            "!",
            ".",
            "،",
            ",",
        ]:
            cleaned = cleaned.replace(symbol, "")

        return cleaned in [
            item.lower()
            for item in self.GREETING_MESSAGES
        ]

    def detect_language(self, text):
        if not text:
            return "ar"

        for character in text:
            if "\u0600" <= character <= "\u06FF":
                return "ar"

        return "en"

    def should_ai_take_priority(self, request):
        if not automation_policy.should_auto_reply_with_ai(
            request.channel,
            request.company_id,
        ):
            return False

        channel_policy = response_policy.get_channel_policy(
            request.channel
        )

        if channel_policy.get("reply_mode") == "flow_only":
            return False

        return True

    def handle_start(self, request, language):
        if self.should_ai_take_priority(request):
            return self.build_main_menu_response(language)

        if request.channel == "telegram":
            session.update(
                request.user_id,
                "state",
                "telegram_iptv_start",
            )

            session.update(
                request.user_id,
                "current_department",
                "iptv",
            )

            return self.render(
                "telegram_iptv_start",
                language,
                request.user_id,
                request.channel,
            )

        session.update(
            request.user_id,
            "state",
            "main_menu",
        )

        return self.build_main_menu_response(language)

    def build_language_changed_response(
        self,
        user_id,
        language,
    ):
        user_session = session.get(user_id) or {}

        current_department = user_session.get(
            "current_department"
        )

        if language == "en":
            if current_department:
                text = (
                    "Language changed to English ✅\n\n"
                    "We will continue the same conversation."
                )

                buttons = self.get_buttons_for_department(
                    current_department,
                    "en",
                )
            else:
                text = (
                    "Language changed to English ✅\n\n"
                    + business_modules.overview_text("en")
                )

                buttons = business_modules.buttons("en")

            if "🏠 Main Menu" not in buttons:
                buttons.append("🏠 Main Menu")

            return Response(text, buttons)

        if current_department:
            text = (
                "تم تغيير اللغة إلى العربية ✅\n\n"
                "سنكمل نفس المحادثة من دون تصفير الموضوع."
            )

            buttons = self.get_buttons_for_department(
                current_department,
                "ar",
            )
        else:
            text = (
                "تم تغيير اللغة إلى العربية ✅\n\n"
                + business_modules.overview_text("ar")
            )

            buttons = business_modules.buttons("ar")

        if "🏠 القائمة الرئيسية" not in buttons:
            buttons.append("🏠 القائمة الرئيسية")

        return Response(text, buttons)

    def build_main_menu_response(self, language):
        if language == "en":
            text = (
                "Welcome to T-ZONE 💙\n\n"
                + business_modules.overview_text("en")
            )

            buttons = business_modules.buttons("en")

            if "Contact support" not in buttons:
                buttons.append("Contact support")

            return Response(text, buttons)

        text = (
            "أهلاً وسهلاً بك في T-ZONE 💙\n\n"
            + business_modules.overview_text("ar")
        )

        buttons = business_modules.buttons("ar")

        if "التواصل مع الدعم" not in buttons:
            buttons.append("التواصل مع الدعم")

        return Response(text, buttons)

    # Default value of the "reply_flow" company setting (see
    # backend/services/company_settings_service.py DEFAULT_SETTINGS).
    # Used only as a last-resort fallback if a stored setting is somehow
    # missing/malformed the canonical default always lives in
    # company_settings_service so the two never drift apart on the happy
    # path (get_section() already merges DEFAULT_SETTINGS in for us).
    DEFAULT_REPLY_FLOW_STEPS = [
        "welcome",
        "language_detection",
        "intent_detection",
        "knowledge_lookup",
        "answer",
        "escalation",
    ]

    def get_reply_flow_steps(self, company_id):
        section = company_settings_service.get_section(
            company_id,
            "reply_flow",
        )

        steps = section.get("values", {}).get("steps")

        if not isinstance(steps, list) or not steps:
            return list(self.DEFAULT_REPLY_FLOW_STEPS)

        return steps

    def handle_ai(
        self,
        request,
        language,
        current_state,
        current_department,
    ):
        if not automation_policy.should_auto_reply_with_ai(
            request.channel,
            request.company_id,
        ):
            return None

        steps = self.get_reply_flow_steps(
            request.company_id
        )

        user_session = session.get(
            request.user_id
        ) or {}

        # Module-button shortcut: the user tapped a business-module menu
        # button. This is a direct navigation action, not one of the
        # reply_flow pipeline steps (welcome/language_detection/
        # intent_detection/knowledge_lookup/answer/escalation), so it stays
        # unconditional regardless of the configured steps.
        module = business_modules.get_module_by_button(
            request.message,
            language,
        )

        if module:
            module_id = module.get("id")

            session.update(
                request.user_id,
                "current_department",
                module_id,
            )

            session.update(
                request.user_id,
                "conversation_topic",
                module_id,
            )

            return self.build_module_response(
                module,
                language,
            )

        welcome_response = self.step_welcome(
            request=request,
            language=language,
            user_session=user_session,
            steps=steps,
        )

        if welcome_response:
            return welcome_response

        # "language_detection" step: the effective language for this turn
        # was already resolved by Engine.handle() (detect_language +
        # session.set_language) before handle_ai() was ever called, and it
        # is threaded through every step below (greeting text, department
        # buttons, AI prompt, safe-fallback copy, welcome injection). There
        # is no separate, independently-skippable "detect the language"
        # action left inside handle_ai() itself -- it is a structural
        # prerequisite for every other step, not an optional pipeline
        # stage, so it cannot be safely gated off even if a company removes
        # "language_detection" from its configured steps. It always runs.
        language = self.step_language_detection(
            request=request,
            language=language,
        )

        current_department = self.step_intent_detection(
            request=request,
            steps=steps,
            current_department=current_department,
        )

        memory_context = conversation_memory.build_context(
            user_session
        )

        channel_policy = (
            response_policy.get_channel_policy(
                request.channel
            )
        )

        selected_knowledge, match_result = self.step_knowledge_lookup(
            request=request,
            language=language,
            steps=steps,
            memory_context=memory_context,
            channel_policy=channel_policy,
        )

        effective_department = (
            match_result.get("department")
            if match_result.get("department") != "unknown"
            else current_department
        )

        connector_results = self.collect_connector_results(
            message=request.message,
            language=language,
            department=effective_department,
            company_id=request.company_id,
        )

        ai_result = self.step_answer(
            request=request,
            language=language,
            current_state=current_state,
            steps=steps,
            memory_context=memory_context,
            selected_knowledge=selected_knowledge,
            connector_results=connector_results,
            channel_policy=channel_policy,
            match_result=match_result,
        )

        if not ai_result:
            safe_result = self.build_safe_result(
                language=language,
                current_department=effective_department,
            )

            return self.finalize_ai_response(
                request=request,
                user_session=user_session,
                ai_result=safe_result,
                steps=steps,
            )

        return self.finalize_ai_response(
            request=request,
            user_session=user_session,
            ai_result=ai_result,
            steps=steps,
        )

    def step_welcome(
        self,
        request,
        language,
        user_session,
        steps,
    ):
        """"welcome" reply_flow step: the greeting-only shortcut.

        If a customer's message is nothing but a greeting, respond
        immediately with the business overview instead of running
        department detection / knowledge lookup / AI generation. Gated on
        "welcome" being present in the company's configured steps -- when
        excluded, a greeting-only message falls through to the rest of the
        pipeline like any other message.
        """
        if "welcome" not in steps:
            return None

        if not self.is_greeting_only(request.message):
            return None

        greeting_result = self.build_greeting_result(
            language
        )

        return self.finalize_ai_response(
            request=request,
            user_session=user_session,
            ai_result=greeting_result,
            steps=steps,
        )

    def step_language_detection(
        self,
        request,
        language,
    ):
        """"language_detection" reply_flow step.

        Structural prerequisite, not a skippable action -- see the
        explanatory comment in handle_ai(). Always returns the
        already-resolved language unchanged.
        """
        return language

    def step_intent_detection(
        self,
        request,
        steps,
        current_department,
    ):
        """"intent_detection" reply_flow step: department detection."""
        if "intent_detection" not in steps:
            return current_department

        detected_department = (
            intent_transition_manager.detect_department(
                request.message
            )
        )

        if detected_department:
            session.update(
                request.user_id,
                "current_department",
                detected_department,
            )

            return detected_department

        return current_department

    def step_knowledge_lookup(
        self,
        request,
        language,
        steps,
        memory_context,
        channel_policy,
    ):
        """"knowledge_lookup" reply_flow step.

        Returns (selected_knowledge, match_result). When skipped, returns
        an empty knowledge selection together with
        ai_knowledge_matcher.empty_result() -- the matcher's own "nothing
        matched" shape -- so downstream department-fallback logic and
        ai_router.route() behave exactly as they already do for a
        no-match turn, without needing special-cased branching.
        """
        if "knowledge_lookup" not in steps:
            return [], ai_knowledge_matcher.empty_result()

        knowledge_items = knowledge_manager.list_for_ai(
            None,
            company_id=request.company_id,
        )

        try:
            max_results = int(
                channel_policy.get(
                    "maximum_knowledge_results",
                    3,
                )
            )
        except (TypeError, ValueError):
            max_results = 3

        max_results = max(
            1,
            min(max_results, 5),
        )

        match_result = ai_knowledge_matcher.match(
            message=request.message,
            language=language,
            items=knowledge_items,
            context=memory_context,
            max_results=max_results,
        )

        selected_knowledge = (
            ai_knowledge_matcher.select_items(
                match_result,
                knowledge_items,
            )
        )

        return selected_knowledge, match_result

    def step_answer(
        self,
        request,
        language,
        current_state,
        steps,
        memory_context,
        selected_knowledge,
        connector_results,
        channel_policy,
        match_result,
    ):
        """"answer" reply_flow step: AI reply generation.

        When "answer" is excluded from steps, no AI call is made and this
        returns None -- handle_ai() already falls back to
        build_safe_result() whenever ai_result is falsy, so a company that
        disables "answer" still gets a safe, human-escalating reply
        instead of silence.
        """
        if "answer" not in steps:
            return None

        return ai_router.route(
            message=request.message,
            channel=request.channel,
            user_id=request.user_id,
            company_id=request.company_id,
            language=language,
            current_state=current_state,
            context=memory_context,
            knowledge=selected_knowledge,
            connector_results=connector_results,
            response_policy=channel_policy,
            match_result=match_result,
        )

    def collect_connector_results(
        self,
        message,
        language,
        department,
        company_id=None,
    ):
        results = []
        lowered = message.lower()

        asks_accounting = self.contains_any(
            lowered,
            [
                "شو علي",
                "شو عليي",
                "حسابي",
                "balance",
                "what do i owe",
                "invoice",
                "فاتورة",
                "ديون",
            ],
        )

        asks_order = self.contains_any(
            lowered,
            [
                "وين طلبي",
                "حالة الطلب",
                "order status",
                "my order",
                "طلبي",
            ],
        )

        asks_product = (
            department == "sales"
            or self.contains_any(
                lowered,
                [
                    "iphone",
                    "samsung",
                    "honor",
                    "tecno",
                    "redmi",
                    "phone",
                    "phones",
                    "laptop",
                    "gaming",
                    "تلفون",
                    "تلفونات",
                    "هاتف",
                    "هواتف",
                    "لابتوب",
                    "لابتوبات",
                    "سعر",
                    "price",
                    "متوفر",
                    "موجود",
                ],
            )
        )

        if asks_accounting:
            result = (
                business_connectors.get_customer_balance(
                    company_id=company_id,
                )
            )

            result["connector"] = "accounting"
            results.append(result)

        if asks_order:
            result = (
                business_connectors.get_order_status(
                    company_id=company_id,
                )
            )

            result["connector"] = "orders"
            results.append(result)

        if asks_product:
            result = (
                business_connectors.get_product_info(
                    message,
                    company_id=company_id,
                )
            )

            result["connector"] = "products"
            results.append(result)

        return results

    def contains_any(
        self,
        text,
        phrases,
    ):
        return any(
            phrase in text
            for phrase in phrases
        )

    def build_module_response(
        self,
        module,
        language,
    ):
        module_id = module.get("id")

        if language == "en":
            text = (
                f"You selected "
                f"{module.get('name_en')}.\n"
                "Tell us what you need exactly."
            )

            buttons = self.get_buttons_for_department(
                module_id,
                "en",
            )

            if "🏠 Main Menu" not in buttons:
                buttons.append("🏠 Main Menu")

            return Response(text, buttons)

        text = (
            f"اخترت قسم "
            f"{module.get('name_ar')}.\n"
            "خبرنا شو بدك تحديداً لنساعدك."
        )

        buttons = self.get_buttons_for_department(
            module_id,
            "ar",
        )

        if "🏠 القائمة الرئيسية" not in buttons:
            buttons.append("🏠 القائمة الرئيسية")

        return Response(text, buttons)

    def build_greeting_result(self, language):
        if language == "en":
            return {
                "department": "information",
                "intent": "greeting",
                "topic": "business_overview",
                "language": "en",
                "confidence": 1.0,
                "reply": (
                    business_modules.overview_text("en")
                ),
                "buttons": (
                    business_modules.buttons("en")
                ),
                "needs_human": False,
                "missing_information": [],
                "used_knowledge_ids": [],
                "notes": (
                    "Greeting handled with "
                    "business modules."
                ),
            }

        return {
            "department": "information",
            "intent": "greeting",
            "topic": "business_overview",
            "language": "ar",
            "confidence": 1.0,
            "reply": (
                business_modules.overview_text("ar")
            ),
            "buttons": (
                business_modules.buttons("ar")
            ),
            "needs_human": False,
            "missing_information": [],
            "used_knowledge_ids": [],
            "notes": (
                "Greeting handled with "
                "business modules."
            ),
        }

    def build_safe_result(
        self,
        language,
        current_department,
    ):
        if language == "en":
            return {
                "department": (
                    current_department
                    or "human_support"
                ),
                "intent": "safe_fallback",
                "topic": (
                    current_department
                    or "unknown"
                ),
                "language": "en",
                "confidence": 1.0,
                "reply": (
                    "I do not have enough confirmed "
                    "information to answer that accurately. "
                    "Please send more detail, or our team "
                    "can check it for you."
                ),
                "buttons": [
                    "Contact support"
                ],
                "needs_human": True,
                "missing_information": [
                    "verified business information"
                ],
                "used_knowledge_ids": [],
                "notes": "Safe fallback.",
            }

        return {
            "department": (
                current_department
                or "human_support"
            ),
            "intent": "safe_fallback",
            "topic": (
                current_department
                or "unknown"
            ),
            "language": "ar",
            "confidence": 1.0,
            "reply": (
                "ما عندي معلومات مؤكدة كافية "
                "حتى جاوبك بدقة. ابعتلنا تفاصيل "
                "أكتر، أو فينا نحولك للفريق "
                "ليتأكدلك."
            ),
            "buttons": [
                "التواصل مع الدعم"
            ],
            "needs_human": True,
            "missing_information": [
                "verified business information"
            ],
            "used_knowledge_ids": [],
            "notes": "Safe fallback.",
        }

    def get_buttons_for_department(
        self,
        department,
        language,
    ):
        if language == "en":
            button_map = {
                "sales": [
                    "Send budget",
                    "Specs",
                    "Contact support",
                ],
                "iptv": [
                    "Renew",
                    "Technical problem",
                    "Contact support",
                ],
                "accounting": [
                    "Verify account",
                    "Contact support",
                ],
                "orders": [
                    "Order number",
                    "Contact support",
                ],
                "maintenance": [
                    "Device type",
                    "Problem",
                    "Contact support",
                ],
                "information": [
                    "Products",
                    "Location",
                    "Contact support",
                ],
            }

            return button_map.get(
                department,
                ["Contact support"],
            )

        button_map = {
            "sales": [
                "إرسال الميزانية",
                "المواصفات",
                "التواصل مع الدعم",
            ],
            "iptv": [
                "تجديد الاشتراك",
                "مشكلة تقنية",
                "التواصل مع الدعم",
            ],
            "accounting": [
                "تأكيد الحساب",
                "التواصل مع الدعم",
            ],
            "orders": [
                "رقم الطلب",
                "التواصل مع الدعم",
            ],
            "maintenance": [
                "نوع الجهاز",
                "المشكلة",
                "التواصل مع الدعم",
            ],
            "information": [
                "المنتجات",
                "العنوان",
                "التواصل مع الدعم",
            ],
        }

        return button_map.get(
            department,
            ["التواصل مع الدعم"],
        )

    def finalize_ai_response(
        self,
        request,
        user_session,
        ai_result,
        steps=None,
    ):
        if steps is None:
            steps = self.DEFAULT_REPLY_FLOW_STEPS

        result_language = (
            ai_result.get("language")
            or self.detect_language(
                request.message
            )
        )

        session.set_language(
            request.user_id,
            result_language,
        )

        session.update(
            request.user_id,
            "last_user_message",
            request.message,
        )

        session.update(
            request.user_id,
            "last_ai_department",
            ai_result.get("department"),
        )

        session.update(
            request.user_id,
            "last_ai_intent",
            ai_result.get("intent"),
        )

        session.update(
            request.user_id,
            "last_ai_confidence",
            ai_result.get("confidence"),
        )

        session.update(
            request.user_id,
            "last_ai_needs_human",
            ai_result.get("needs_human"),
        )

        session.update(
            request.user_id,
            "last_ai_reply",
            ai_result.get("reply"),
        )

        session.update(
            request.user_id,
            "conversation_topic",
            ai_result.get("topic"),
        )

        session.update(
            request.user_id,
            "last_missing_information",
            ai_result.get(
                "missing_information",
                [],
            ),
        )

        session.update(
            request.user_id,
            "last_used_knowledge_ids",
            ai_result.get(
                "used_knowledge_ids",
                [],
            ),
        )

        if ai_result.get("department"):
            session.update(
                request.user_id,
                "current_department",
                ai_result.get("department"),
            )

            session.update(
                request.user_id,
                "state",
                (
                    f"{ai_result.get('department')}"
                    f"_ai"
                ),
            )

        reply, buttons = response_policy.compose_reply(
            channel=request.channel,
            user_session=user_session,
            ai_result=ai_result,
        )

        conversation_memory.append(
            user_session,
            "assistant",
            reply,
        )

        # "escalation" reply_flow step: only surface the human-handoff
        # button/state when the AI itself flagged needs_human AND the
        # company's configured steps still include "escalation". Removing
        # "escalation" from steps suppresses the button even when
        # needs_human is true -- it does not change needs_human itself,
        # which stays recorded on the session above.
        if ai_result.get("needs_human"):
            if result_language == "ar":
                support_label = (
                    "التواصل مع الدعم"
                )
            else:
                support_label = (
                    "Contact support"
                )

            if "escalation" in steps:
                if support_label not in buttons:
                    buttons.append(support_label)
            else:
                # build_safe_result() (and, potentially, the AI's own
                # JSON response) can already bake the support label
                # straight into ai_result["buttons"] whenever
                # needs_human is true -- not just via the append above --
                # so disabling "escalation" must also strip it from
                # whatever compose_reply() already produced, in both
                # languages, or the button would still leak through.
                buttons = [
                    button
                    for button in buttons
                    if button not in (
                        "Contact support",
                        "التواصل مع الدعم",
                    )
                ]

        if request.channel != "telegram":
            if result_language == "ar":
                menu_label = (
                    "🏠 القائمة الرئيسية"
                )
            else:
                menu_label = (
                    "🏠 Main Menu"
                )

            if menu_label not in buttons:
                buttons.append(menu_label)

        return Response(
            reply,
            buttons,
        )

    def handle_intent(
        self,
        request,
        language,
    ):
        intent = intent_detector.detect(
            request.message
        )

        next_state = (
            intent_detector.get_state_for_intent(
                intent
            )
        )

        if not next_state:
            return None

        session.update(
            request.user_id,
            "last_intent",
            intent,
        )

        session.update(
            request.user_id,
            "state",
            next_state,
        )

        session.update(
            request.user_id,
            "current_department",
            "iptv",
        )

        session.push_state(
            request.user_id,
            next_state,
        )

        return self.render(
            next_state,
            language,
            request.user_id,
            request.channel,
        )

    def handle_input_state(
        self,
        request,
        state_data,
        language,
    ):
        input_data = state_data["input"]

        session.update(
            request.user_id,
            input_data["key"],
            request.message,
        )

        next_state = input_data.get("next")

        session.push_state(
            request.user_id,
            next_state,
        )

        if next_state == "iptv_ticket_created":
            self.create_ticket(request)

        return self.render(
            next_state,
            language,
            request.user_id,
            request.channel,
        )

    def handle_button_state(
        self,
        request,
        state_data,
        language,
    ):
        for button in state_data.get(
            "buttons",
            [],
        ):
            label = button.get(language)

            if request.message != label:
                continue

            for key, value in button.get(
                "set",
                {},
            ).items():
                session.update(
                    request.user_id,
                    key,
                    value,
                )

            next_state = button.get("next")

            if not next_state:
                return None

            session.push_state(
                request.user_id,
                next_state,
            )

            user_session = (
                session.get(request.user_id)
                or {}
            )

            new_language = (
                user_session.get("language")
                or language
            )

            return self.render(
                next_state,
                new_language,
                request.user_id,
                request.channel,
            )

        return None

    def invalid_choice(
        self,
        current_state,
        language,
        user_id,
        channel,
    ):
        response = self.render(
            current_state,
            language,
            user_id,
            channel,
        )

        warning = self.INVALID_CHOICE_TEXT.get(
            language,
            self.INVALID_CHOICE_TEXT["ar"],
        )

        response.text = (
            f"{warning}\n\n"
            f"{response.text}"
        )

        return response

    def create_ticket(self, request):
        user_session = (
            session.get(request.user_id)
            or {}
        )

        ticket_id = db.create_ticket({
            "platform": request.channel,
            "user_id": request.user_id,
            "language": user_session.get(
                "language"
            ),
            "iptv_username": user_session.get(
                "iptv_username"
            ),
            "device": user_session.get(
                "device"
            ),
            "os": user_session.get("os"),
            "app": user_session.get("app"),
            "problem": user_session.get(
                "problem"
            ),
        })

        session.update(
            request.user_id,
            "ticket_id",
            ticket_id,
        )

    def render(
        self,
        state_name,
        language,
        user_id,
        channel,
    ):
        if not state_name:
            return Response(
                self.ERROR_TEXT.get(
                    language,
                    self.ERROR_TEXT["ar"],
                ),
                [],
            )

        state_data = flow_loader.get_state(
            state_name
        )

        if not state_data:
            logger.error(
                "Render failed. State not found: %s",
                state_name,
            )

            return Response(
                self.ERROR_TEXT.get(
                    language,
                    self.ERROR_TEXT["ar"],
                ),
                [],
            )

        text = self.get_text(
            state_name,
            state_data,
            language,
        )

        text = self.fill_placeholders(
            text,
            user_id,
        )

        buttons = []

        for button in state_data.get(
            "buttons",
            [],
        ):
            label = button.get(language)

            if (
                label
                and label not in self.BACK_BUTTONS
                and label not in self.HOME_BUTTONS
            ):
                buttons.append(label)

        if self.should_add_navigation(
            state_name
        ):
            if language == "en":
                buttons.append("⬅️ Back")
                buttons.append(
                    "🏠 Back to Main Menu"
                )
            else:
                buttons.append("⬅️ رجوع")
                buttons.append(
                    "🏠 العودة إلى القائمة الرئيسية"
                )

        return Response(
            text,
            buttons,
        )

    def get_text(
        self,
        state_name,
        state_data,
        language,
    ):
        if state_name == "iptv_faq":
            faq_text = (
                content_loader.get_faq_text(
                    "iptv",
                    language,
                )
            )

            if faq_text:
                return faq_text

        if state_name in self.SOLUTION_STATES:
            solution_id = (
                self.SOLUTION_STATES[
                    state_name
                ]
            )

            solution_text = (
                content_loader.get_solution_text(
                    "iptv",
                    solution_id,
                    language,
                )
            )

            if solution_text:
                return solution_text

        text_data = state_data.get(
            "text",
            {},
        )

        return (
            text_data.get(language)
            or text_data.get("ar")
            or text_data.get("en")
            or ""
        )

    def should_add_navigation(
        self,
        state_name,
    ):
        return (
            state_name
            not in self.SYSTEM_STATES
        )

    def fill_placeholders(
        self,
        text,
        user_id,
    ):
        user_session = (
            session.get(user_id)
            or {}
        )

        replacements = {
            "{{ticket_id}}": str(
                user_session.get(
                    "ticket_id"
                ) or "-"
            ),
            "{{iptv_username}}": str(
                user_session.get(
                    "iptv_username"
                ) or "-"
            ),
            "{{device}}": str(
                user_session.get(
                    "device"
                ) or "-"
            ),
            "{{os}}": str(
                user_session.get("os")
                or "-"
            ),
            "{{app}}": str(
                user_session.get("app")
                or "-"
            ),
            "{{problem}}": str(
                user_session.get(
                    "problem"
                ) or "-"
            ),
        }

        for key, value in replacements.items():
            text = text.replace(
                key,
                value,
            )

        return text

    def safe_language(self, user_id):
        user_session = (
            session.get(user_id)
            or {}
        )

        return (
            user_session.get("language")
            or "ar"
        )

    def log_request(
        self,
        request,
        current_state,
    ):
        logger.info(
            (
                "channel=%s user=%s "
                "state=%s message=%s"
            ),
            request.channel,
            request.user_id,
            current_state,
            request.message,
        )


engine = Engine()