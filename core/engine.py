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
from core.ai_knowledge_matcher import ai_knowledge_matcher
from core.response_policy import response_policy
from core.business_connectors import business_connectors
from core.business_modules import business_modules
from core import reply_decision
from backend.services.conversation_control_service import conversation_control_service
from backend.services.knowledge_service import knowledge_service
from backend.services.module_gate import module_gate
from backend.services.ticket_service import ticket_service


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
            user_session = session.create(request.user_id)

            if self.is_reset_message(request.message):
                session.reset(request.user_id)

                language = self.detect_language(request.message)
                session.set_language(request.user_id, language)

                return self.build_main_menu_response(
                    language,
                    company_id=request.company_id,
                )

            explicit_language = self.get_explicit_language(request.message)

            if explicit_language:
                session.set_language(
                    request.user_id,
                    explicit_language,
                )

                return self.build_language_changed_response(
                    user_id=request.user_id,
                    language=explicit_language,
                    company_id=request.company_id,
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

                return self.build_main_menu_response(
                    language,
                    company_id=request.company_id,
                )

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

            # The session first because it is the cheapest, then the
            # conversation row — which is where the department actually lives.
            # A restart empties the session, and a customer whose choice was
            # only ever in memory would silently fall back to unrouted.
            current_department = user_session.get(
                "current_department"
            ) or self.stored_department(request)

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

                return self.build_main_menu_response(
                    language,
                    company_id=request.company_id,
                )

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
        return automation_policy.should_auto_reply_with_ai(
            request.channel
        )

    def handle_start(self, request, language):
        if automation_policy.should_auto_reply_with_ai(
            request.channel
        ):
            return self.build_main_menu_response(
                language,
                company_id=request.company_id,
            )

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

        return self.build_main_menu_response(
            language,
            company_id=request.company_id,
        )

    def build_language_changed_response(
        self,
        user_id,
        language,
        company_id=None,
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
                text = self.join_lines(
                    "Language changed to English ✅",
                    business_modules.overview_text(
                        company_id,
                        "en",
                    ),
                )

                buttons = business_modules.buttons(
                    company_id,
                    "en",
                )

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
            text = self.join_lines(
                "تم تغيير اللغة إلى العربية ✅",
                business_modules.overview_text(
                    company_id,
                    "ar",
                ),
            )

            buttons = business_modules.buttons(
                company_id,
                "ar",
            )

        if "🏠 القائمة الرئيسية" not in buttons:
            buttons.append("🏠 القائمة الرئيسية")

        return Response(text, buttons)

    # The menu is assembled entirely from what the asking company has actually
    # written down. It used to open with "Welcome to T-ZONE 💙" and list
    # T-ZONE's departments, hardcoded here, so every business on the platform
    # greeted its customers as another business and offered them another
    # business's sections.
    #
    # Both halves are now optional and independently omitted:
    #
    # * The greeting is the company's own welcome message from its assistant
    #   profile. A company that wrote none is not given one.
    # * The sections sentence and its buttons appear only if the company has
    #   defined departments. With none, the menu carries neither rather than a
    #   fabricated list.
    #
    # If a company has written neither, the reply still has to say *something* —
    # an empty message cannot be delivered — so it falls back to a question that
    # makes no claim about the business and names nobody.
    NEUTRAL_MENU_PROMPT = {
        "en": "How can we help you today?",
        "ar": "كيف فينا نساعدك اليوم؟",
    }

    SUPPORT_BUTTON = {
        "en": "Contact support",
        "ar": "التواصل مع الدعم",
    }

    def build_main_menu_response(
        self,
        language,
        company_id=None,
        channel="messenger",
        channel_account_id=None,
    ):
        language = "en" if language == "en" else "ar"

        greeting = response_policy.get_welcome_message(
            channel,
            language,
            company_id=company_id,
            channel_account_id=channel_account_id,
        )

        overview = business_modules.overview_text(
            company_id,
            language,
        )

        buttons = business_modules.buttons(
            company_id,
            language,
        )

        text = self.join_lines(greeting, overview)

        if not text:
            text = self.NEUTRAL_MENU_PROMPT[language]

        support_label = self.SUPPORT_BUTTON[language]

        if support_label not in buttons:
            buttons.append(support_label)

        return Response(text, buttons)

    @staticmethod
    def join_lines(*parts):
        """Join the parts that exist, with a blank line between them.

        Concatenating unconditionally left a trailing blank paragraph whenever a
        company had no sections to list.
        """
        return "\n\n".join(
            part.strip()
            for part in parts
            if part and part.strip()
        )

    # ------------------------------------------------------------------
    # Where a conversation belongs
    # ------------------------------------------------------------------
    #
    # The department a conversation is in has to outlive the process. It used to
    # live only in `core/session.py`, which is an in-memory dictionary with a
    # six-hour eviction: a customer who chose "Bookings" from the menu was in
    # Bookings until the next deploy, and in nothing afterwards. Meanwhile the
    # column an employee reads in the inbox was written only by an employee, so
    # the assistant's routing and the team's view of it never met.
    #
    # Both are now written, in the order the owner specified — the customer's
    # own choice first, then the account's default, then the model's
    # classification. The account's default is applied when the conversation is
    # created (`conversation_control_service.get_or_create`); the other two are
    # applied here.
    #
    # Nothing on this path may create a conversation or fail a reply:
    # `assign_department` updates an existing row or does nothing, and every
    # call is wrapped, because a routing decision is never worth a customer's
    # answer.

    def remember_department(
        self,
        request,
        code,
        source,
        only_if_unassigned=False,
    ):
        if not code:
            return

        applied = code

        if getattr(request, "company_id", None):
            try:
                state = conversation_control_service.assign_department(
                    company_id=request.company_id,
                    channel=request.channel,
                    external_user_id=request.user_id,
                    code=code,
                    source=source,
                    only_if_unassigned=only_if_unassigned,
                )

                # The session follows the row, never the other way round. A
                # model guess that was correctly refused because the customer
                # had already chosen must not win in memory instead.
                if state and state.get("department_id"):
                    applied = state.get("department") or code
            except Exception:
                logger.exception(
                    "Could not record department %s for company %s",
                    code,
                    request.company_id,
                )

        session.update(
            request.user_id,
            "current_department",
            applied,
        )

    def stored_department(self, request):
        """The department already on the conversation row, if any.

        Read when the session has none, which is the ordinary case after a
        restart. Without this the durable choice would be invisible to the very
        engine that has to honour it, and the customer would be asked to choose
        again.
        """
        if not getattr(request, "company_id", None):
            return None

        try:
            state = conversation_control_service.find_state(
                company_id=request.company_id,
                channel=request.channel,
                external_user_id=request.user_id,
            )
        except Exception:
            logger.exception(
                "Could not read the department of a conversation for company %s",
                request.company_id,
            )

            return None

        if not state or not state.get("department_id"):
            return None

        return state.get("department")

    def load_company_knowledge(
        self,
        request,
        department=None,
    ):
        """Load the knowledge of the company this message belongs to.

        The assistant used to read two shared JSON files, so every company on
        the platform answered its customers out of one company's knowledge.
        Items now come from ``knowledge_items`` inside the owning company's own
        encrypted database.

        Two failure modes are handled here rather than left to explode. A
        request with no company cannot be answered from anyone's knowledge, and
        guessing a company is exactly the leak this replaces. And a knowledge
        database that will not open must not take the reply path down with it:
        with no knowledge the router's guardrails already escalate to a human.
        """
        company_id = getattr(
            request,
            "company_id",
            None,
        )

        if not company_id:
            logger.warning(
                "Message on channel %s has no company; "
                "the assistant runs with no knowledge.",
                getattr(request, "channel", "unknown"),
            )

            return []

        # A company that switched Knowledge off gets an assistant that does not
        # have one. The switch used to hide the screen from the team while the
        # assistant went on answering out of the base behind it, which made the
        # owner's decision cosmetic. No knowledge is a supported state already:
        # the router's guardrails escalate to a human rather than invent facts.
        if not module_gate.enabled(company_id, "knowledge"):
            return []

        try:
            return knowledge_service.for_assistant(
                company_id,
                department,
            )
        except Exception:
            logger.exception(
                "Could not load knowledge for company %s",
                company_id,
            )

            return []

    def handle_ai(
        self,
        request,
        language,
        current_state,
        current_department,
    ):
        if not automation_policy.should_auto_reply_with_ai(
            request.channel
        ):
            return None

        user_session = session.get(
            request.user_id
        ) or {}

        module = business_modules.get_module_by_button(
            request.company_id,
            request.message,
            language,
        )

        if module:
            module_id = module.get("id")

            # The customer chose this from the menu the company defined. It is
            # the most specific signal there is, so it overwrites whatever the
            # account defaulted to and whatever the model previously guessed.
            self.remember_department(
                request,
                module_id,
                source="customer_choice",
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

        if self.is_greeting_only(request.message):
            greeting_result = self.build_greeting_result(
                language,
                company_id=request.company_id,
            )

            return self.finalize_ai_response(
                request=request,
                user_session=user_session,
                ai_result=greeting_result,
            )

        # Matched against this company's own sections and nothing else. This
        # used to consult a hardcoded Arabic keyword table describing one
        # company's products, applied to every company's customers.
        detected_department = (
            intent_transition_manager.detect_department(
                request.message,
                company_id=request.company_id,
            )
        )

        if detected_department:
            # Typing the name of a section is the customer choosing it, the
            # same as pressing its button.
            self.remember_department(
                request,
                detected_department,
                source="customer_choice",
            )

            current_department = detected_department

        memory_context = conversation_memory.build_context(
            user_session
        )

        # This company's own reply mechanism, resolved for this channel: the
        # platform's shipped defaults, then whatever this company chose. The
        # company is passed explicitly, like it is to ``ai_router.route`` and
        # ``collect_connector_results`` below.
        channel_policy = (
            response_policy.get_channel_policy(
                request.channel,
                company_id=request.company_id,
            )
        )

        knowledge_items = self.load_company_knowledge(
            request
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

        # This company's five remaining switches, applied. Until now
        # `reply_mode`, `grounded_ai_enabled`, `allow_ai_free_reply`,
        # `minimum_match_confidence` and `fallback_to_human` were resolved,
        # merged and serialised into the model payload without anything
        # consulting them: an owner could set "keep it to what you taught it",
        # watch it save, and get an assistant that answered whatever it liked.
        decision = reply_decision.decide(
            channel_policy,
            match_result,
            has_knowledge=bool(knowledge_items),
        )

        if not decision.use_knowledge:
            selected_knowledge = []

        if decision.blocked:
            # No model call at all. Nothing here has cost the platform a model
            # charge yet, which is the other half of honouring the switch.
            return self.finalize_ai_response(
                request=request,
                user_session=user_session,
                ai_result=self.build_safe_result(
                    language=language,
                    current_department=(
                        match_result.get("department")
                        if match_result.get("department") != "unknown"
                        else current_department
                    ),
                    escalate=decision.escalate,
                    reason=decision.reason,
                ),
            )

        connector_results = self.collect_connector_results(
            message=request.message,
            language=language,
            department=(
                match_result.get("department")
                if match_result.get("department") != "unknown"
                else current_department
            ),
            company_id=request.company_id,
        )

        ai_result = ai_router.route(
            message=request.message,
            channel=request.channel,
            user_id=request.user_id,
            language=language,
            current_state=current_state,
            context=memory_context,
            knowledge=selected_knowledge,
            connector_results=connector_results,
            # The resolved decision rather than the raw switches. The model is
            # told what it may actually do on this message: a policy saying
            # `allow_ai_free_reply: true` alongside a decision that refused one
            # is an instruction to do the thing the owner just forbade.
            response_policy={
                **channel_policy,
                "allow_ai_free_reply": decision.allow_free_reply,
                "grounded_ai_enabled": decision.use_knowledge,
            },
            match_result=match_result,
            company_id=request.company_id,
            channel_account_id=getattr(request, "channel_account_id", None),
        )

        if not ai_result:
            safe_result = self.build_safe_result(
                language=language,
                current_department=(
                    match_result.get("department")
                    if match_result.get("department") != "unknown"
                    else current_department
                ),
                escalate=reply_decision.fallback_to_human(channel_policy),
                reason="model_returned_nothing",
            )

            return self.finalize_ai_response(
                request=request,
                user_session=user_session,
                ai_result=safe_result,
            )

        return self.finalize_ai_response(
            request=request,
            user_session=user_session,
            ai_result=ai_result,
        )

    def collect_connector_results(
        self,
        message,
        language,
        department,
        company_id=None,
    ):
        """Gather verified facts the assistant is allowed to state.

        The company must be threaded through: a product lookup answers with real
        prices, and without knowing whose catalogue to read the connector
        refuses rather than guessing — a guess here would quote one company's
        price to another company's customer.
        """
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
                business_connectors.get_customer_balance()
            )

            result["connector"] = "accounting"
            results.append(result)

        if asks_order:
            result = (
                business_connectors.get_order_status()
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
            # A company may name a section in one language only; printing the
            # literal "None" back at the customer is worse than the other name.
            name = (
                module.get("name_en")
                or module.get("name_ar")
                or module_id
            )

            text = (
                f"You selected "
                f"{name}.\n"
                "Tell us what you need exactly."
            )

            buttons = self.get_buttons_for_department(
                module_id,
                "en",
            )

            if "🏠 Main Menu" not in buttons:
                buttons.append("🏠 Main Menu")

            return Response(text, buttons)

        arabic_name = (
            module.get("name_ar")
            or module.get("name_en")
            or module_id
        )

        text = (
            f"اخترت قسم "
            f"{arabic_name}.\n"
            "خبرنا شو بدك تحديداً لنساعدك."
        )

        buttons = self.get_buttons_for_department(
            module_id,
            "ar",
        )

        if "🏠 القائمة الرئيسية" not in buttons:
            buttons.append("🏠 القائمة الرئيسية")

        return Response(text, buttons)

    def build_greeting_result(self, language, company_id=None):
        # "hello" is answered with what this company actually offers. A company
        # that has defined no sections has nothing to list, so the reply falls
        # back to asking what the customer needs rather than reciting another
        # company's departments.
        overview = business_modules.overview_text(
            company_id,
            "en" if language == "en" else "ar",
        )

        if language == "en":
            return {
                "department": "information",
                "intent": "greeting",
                "topic": "business_overview",
                "language": "en",
                "confidence": 1.0,
                "reply": (
                    overview
                    or self.NEUTRAL_MENU_PROMPT["en"]
                ),
                "buttons": (
                    business_modules.buttons(
                        company_id,
                        "en",
                    )
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
                overview
                or self.NEUTRAL_MENU_PROMPT["ar"]
            ),
            "buttons": (
                business_modules.buttons(
                    company_id,
                    "ar",
                )
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
        escalate=True,
        reason=None,
    ):
        """The reply for a message the company's rules leave no answer for.

        ``escalate`` is the company's ``fallback_to_human`` switch. On, this
        hands the conversation to a human and offers the support button. Off,
        the customer is told plainly that the information is not confirmed and
        the conversation stays where it is — which is what "answers anyway
        instead of escalating" means for a message that has no answer.

        The wording does not change between the two. A company that switched
        escalation off did not ask its assistant to start guessing; it asked it
        to stop handing conversations over.
        """
        if language == "en":
            result = {
                "department": (
                    current_department
                    or ("human_support" if escalate else "unknown")
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
                "buttons": (
                    ["Contact support"] if escalate else []
                ),
                "needs_human": bool(escalate),
                "missing_information": [
                    "verified business information"
                ],
                "used_knowledge_ids": [],
                "notes": "Safe fallback.",
            }
        else:
            result = {
                "department": (
                    current_department
                    or ("human_support" if escalate else "unknown")
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
                "buttons": (
                    ["التواصل مع الدعم"] if escalate else []
                ),
                "needs_human": bool(escalate),
                "missing_information": [
                    "verified business information"
                ],
                "used_knowledge_ids": [],
                "notes": "Safe fallback.",
            }

        if reason:
            # Which switch produced the silence. Without it an owner who
            # tightened `minimum_match_confidence` too far sees an assistant
            # that stopped answering and nothing that says why.
            result["notes"] = f"Safe fallback ({reason})."

        return result

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
    ):
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
            # Least specific of the three, so it is written only when nothing
            # more specific has claimed the conversation: a model guess must
            # never displace the customer's own choice or the department the
            # receiving account feeds.
            self.remember_department(
                request,
                ai_result.get("department"),
                source="ai_classification",
                only_if_unassigned=True,
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
            company_id=request.company_id,
            channel_account_id=getattr(
                request,
                "channel_account_id",
                None,
            ),
        )

        conversation_memory.append(
            user_session,
            "assistant",
            reply,
        )

        if ai_result.get("needs_human"):
            if result_language == "ar":
                support_label = (
                    "التواصل مع الدعم"
                )
            else:
                support_label = (
                    "Contact support"
                )

            if support_label not in buttons:
                buttons.append(support_label)

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
        # Tasks off means no ticket is opened. A company that switched the
        # module off cannot see, assign or close a ticket, so writing one would
        # bury the customer's problem in a table nobody on that team can open —
        # worse than not recording it, because the flow tells the customer a
        # ticket exists.
        if not module_gate.enabled(
            getattr(request, "company_id", None),
            "tasks",
        ):
            logger.info(
                "Tasks is off for company %s; no ticket was opened for %s.",
                getattr(request, "company_id", None),
                request.user_id,
            )

            return

        user_session = (
            session.get(request.user_id)
            or {}
        )

        ticket_id = ticket_service.create(
            company_id=request.company_id,
            data={
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
            },
        )

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
                "state=%s message_length=%s"
            ),
            request.channel,
            request.user_id,
            current_state,
            len(request.message or ""),
        )


engine = Engine()