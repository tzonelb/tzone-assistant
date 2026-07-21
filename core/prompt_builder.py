import json
from typing import Any

from core.profile_loader import profile_loader
from core.automation_policy import automation_policy


class PromptBuilder:
    def build_system_prompt(self, channel: str) -> str:
        company = profile_loader.get_company()
        modules = profile_loader.get_modules()
        channel_role = profile_loader.get_channel_role(channel)
        ai_style = profile_loader.get_ai_style()
        channel_policy = automation_policy.get_channel_policy(channel)

        context = {
            "company": company,
            "business_modules": modules,
            "channel": channel,
            "channel_role": channel_role,
            "ai_style": ai_style,
            "automation_policy": channel_policy,
        }

        return f"""
You are the AI routing and reply brain for this business platform.

Use this business configuration:
{json.dumps(context, ensure_ascii=False, indent=2)}

Core rules:
- Use the company/profile configuration as source of truth.
- Do not invent prices, stock, offers, warranty, availability, policies, or product recommendations.
- If stock or price is needed and not provided, ask for budget/specifications or route to human support.
- Continue the conversation using context.
- Do not greet again if the conversation already started.
- Reply in the same language as the user.
- Keep replies short and helpful.
- Buttons must match the current topic.
- Return valid JSON only.

Required JSON:
{{
  "department": "sales|iptv|maintenance|accounting|telecom|information|human_support|unknown",
  "intent": "short_intent_name",
  "topic": "short_conversation_topic",
  "language": "ar|en|unknown",
  "confidence": 0.0,
  "reply": "safe customer-facing reply",
  "buttons": ["button1", "button2"],
  "needs_human": false,
  "notes": "internal note"
}}
""".strip()


prompt_builder = PromptBuilder()