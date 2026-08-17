"""Scripted conversation flows, and whose they are.

`features/*/flow.json` holds T-ZONE's own IPTV support script — a language
picker, a menu reading "📺 IPTV · 🛍️ Sales · ℹ️ About T-ZONE", and the
troubleshooting steps behind it. It was loaded once at import and served to
everybody.

That was not theoretical. `config/automation_policy.json` shipped WhatsApp as
`meta_agent_only` and Telegram as `flow_only`, so on those two channels the AI
never took priority and the engine fell through to exactly this loader — which
answered **every company's** WhatsApp and Telegram customers with T-ZONE's own
menu. Running the engine for an arbitrary company id returns that menu verbatim.

It is the same defect the platform already fixed twice, on the other path: the
shared `bot_profile.json` that put one company's persona in everybody's prompt
(D-005), and the shared branding and menu in the AI reply. Both were moved into
each company's own encrypted database. The flow was not, and nothing noticed
because the flow only reaches a customer on the two channels nobody was testing.

### The rule now

A flow is a company's own script, not platform code. This loader will only ever
hand the shipped `features/` flows to a **single-company installation** — the
same test `channels/credentials.py` already uses to decide whether the
environment's access token may stand in for a connected account. As soon as a
second company exists there is no safe answer, so the answer is none.

A company with no flow of its own gets no flow, and the engine falls through to
the assistant — which reads that company's own departments, knowledge and
profile. That is already how Messenger and Instagram work for everyone, so it is
not a new behaviour; it is the existing one, applied to the two channels that
were missing it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


FEATURES_DIR = Path("features")


class FlowLoader:
    def __init__(self) -> None:
        self.states: dict[str, Any] = {}
        self.load_all_flows()

    def load_all_flows(self) -> None:
        """Read the shipped flows from disk.

        Still loaded, because a single-company installation legitimately uses
        them and because the AI-teaching preview reads the opening state to show
        an owner what their assistant starts from. Who they may be served to is
        decided in `get_state`, not here.
        """
        self.states = {}

        if not FEATURES_DIR.exists():
            return

        for flow_file in sorted(FEATURES_DIR.glob("*/flow.json")):
            try:
                with open(flow_file, "r", encoding="utf-8") as file:
                    data = json.load(file)
            except (OSError, json.JSONDecodeError):
                # One malformed file must not leave the platform with no flows
                # at all — and on a single-company install that would mean the
                # bot answering nothing.
                logger.exception("Could not read the flow file %s", flow_file)
                continue

            self.states.update(data.get("states", {}))

    def get_state(self, state_name: str, company_id: Any = None) -> dict | None:
        """The scripted state, if this company is entitled to it.

        ``company_id`` is required in anything customer-facing. It defaults to
        ``None`` for the preview and for tests, and ``None`` means "no company
        asked", which is served the shipped flows: a caller with no company is
        not a customer of anyone.
        """
        if not state_name:
            return None

        if company_id is not None and not self._may_serve(company_id):
            return None

        return self.states.get(state_name)

    @staticmethod
    def _may_serve(company_id: Any) -> bool:
        """Whether the shipped flows belong to this company.

        Only on a single-company installation, and only for that company.
        `default_company_id` returns ``None`` the moment a second company
        exists, which is what stops one company's script from reaching another
        company's customers.

        Fails **closed**: if the question cannot be answered, no flow is served.
        The other direction would answer a customer with somebody else's menu,
        and a company whose assistant falls through to the AI path still gets a
        reply — from its own knowledge.
        """
        from database.manager import database_manager

        try:
            return database_manager.default_company_id() == int(company_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not determine whether company %s owns the shipped flows; "
                "serving none",
                company_id,
            )

            return False


flow_loader = FlowLoader()
