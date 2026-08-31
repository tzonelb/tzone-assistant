"""The sample data a demonstration workspace opens with, and how it goes away.

A demonstration that opens empty demonstrates nothing: an inbox with no
conversations, a reporting screen with no numbers and a catalogue with no
products tell a prospective customer that the platform does not work. So a
workspace created by sign-up arrives with a few days of plausible traffic.

**Written through the real services, never by INSERT.** `save_message`,
`create_customer`, `create_product`, `create_item` -- the same writers a live
message goes through. A hand-written INSERT would produce rows with the right
columns and the wrong shape, and the screens would render them slightly wrong
in ways nobody would notice until a real customer's data looked different from
the sample they were shown. Only `created_at` is rewritten afterwards, because
the writers stamp the clock and a week of conversation that all happened in one
second gives the reporting screen nothing to draw.

**Every row it writes is recorded, so activation can take exactly it away.**
This is the part worth being careful about. A workspace that becomes real must
not keep six invented customers among its real ones -- an owner who cannot tell
which of their conversations happened has a reporting screen that lies to them.
And "delete everything from before the activation" is the wrong rule too,
because they will have added real things while trying the platform out. So the
seeder writes a ledger of table and row id, and activation deletes that list
and nothing else.

**The sample is deliberately imperfect.** One customer nobody ever answered,
one reply that took two hours, one product out of stock, one archived. A
demonstration where every wait is instant and every shelf is full shows none of
the screens that exist to find problems, which are the screens worth showing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)



def _minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# `sender_type` is load-bearing, not decoration: reporting counts `ai` as an
# assistant reply and `employee` as a person, and a seed that wrote anything
# else would show a workspace whose assistant answered nothing. The preview
# fixtures were wrong in exactly this way once.
_CONVERSATIONS: tuple[tuple[str, str, str, tuple, ...], ...] = (
    (
        "messenger",
        "demo-lina",
        "Lina Khoury",
        (
            ("in", "مرحبا، بدي اعرف اذا في توصيل عالمنصورية؟", "customer", 60 * 24 * 6),
            ("out", "أهلاً لينا! نعم منوصّل، الرسوم ٣ دولار والتوصيل خلال ٢٤ ساعة.", "ai", 60 * 24 * 6 - 1),
            ("in", "تمام، وكم سعر الباقة الشهرية؟", "customer", 60 * 24 * 6 - 4),
            ("out", "الباقة الشهرية ٢٥ دولار، وفيها تركيب مجاني.", "employee", 60 * 24 * 6 - 20),
        ),
    ),
    (
        "whatsapp",
        "demo-omar",
        "Omar Saad",
        (
            ("in", "Hi, is the small package still available?", "customer", 60 * 24 * 4),
            ("out", "Hello Omar — yes it is. Shall I reserve one for you?", "ai", 60 * 24 * 4 - 1),
            ("in", "Yes please, under my name.", "customer", 60 * 24 * 4 - 5),
            ("out", "Done — reserved until Friday.", "employee", 60 * 24 * 4 - 12),
        ),
    ),
    (
        "instagram",
        "demo-nour",
        "Nour Aoun",
        (
            ("in", "شفت البوست تبع العرض، لسا شغال؟", "customer", 60 * 24 * 2),
            ("out", "أهلاً نور! العرض شغال لآخر الشهر.", "ai", 60 * 24 * 2 - 1),
        ),
    ),
    (
        "telegram",
        "demo-karim",
        "Karim Fares",
        (
            ("in", "Do you have an English catalogue?", "customer", 60 * 26),
            # Two hours before anybody answered, on purpose. A demonstration
            # where every wait is instant cannot show what the wait
            # distribution and the "longest waits" list are for.
            ("out", "We do — sending it over, sorry for the wait.", "employee", 60 * 24),
        ),
    ),
    # Nobody ever replied to Rami. The most important row on the reporting
    # screen is the customer who wrote and got nothing back.
    (
        "whatsapp",
        "demo-rami",
        "Rami Daher",
        (
            ("in", "بعتلكن مبارح وما وصلني جواب، بدي اعرف اذا الطلب جهز", "customer", 60 * 8),
        ),
    ),
)

_PRODUCTS: tuple[dict[str, Any], ...] = (
    {"name": "غسالة أوتوماتيك ٨ كيلو", "sku": "DEMO-WM-8", "price": 429.0, "in_stock": 1},
    {"name": "براد بابين", "sku": "DEMO-FR-2D", "price": 615.0, "sale_price": 549.0, "in_stock": 1},
    {"name": "مكيف ١٢ ألف", "sku": "DEMO-AC-12", "price": 380.0, "in_stock": 0},
    {"name": "فرن كهربائي", "sku": "DEMO-OV-60", "price": 240.0, "in_stock": 1, "status": "archived"},
)

# `content_ar` / `content_en`, not `content`: `knowledge_service.create_item`
# keeps a column per language and refuses an item that has neither, because an
# item with no content teaches the assistant nothing.
_KNOWLEDGE: tuple[dict[str, Any], ...] = (
    {
        "title": "التوصيل والرسوم",
        "content_ar": "التوصيل داخل بيروت مجاني. خارج بيروت ٣ دولار، خلال ٢٤ ساعة.",
        "content_en": "Delivery inside Beirut is free. Outside Beirut is $3, within 24 hours.",
        "keywords": "توصيل, delivery, رسوم",
    },
    {
        "title": "الضمان",
        "content_ar": "كل الأجهزة عليها ضمان سنة كاملة يشمل قطع الغيار واليد العاملة.",
        "content_en": "Every appliance carries a full year of warranty, parts and labour.",
        "keywords": "ضمان, warranty",
    },
)


class DemoSeedService:
    def seed(self, *, company_id: int, owner_user_id: int) -> dict[str, int]:
        """Fill a new demonstration workspace, and record what was filled.

        Never raises into the caller: a sign-up that provisioned a workspace
        and then failed while decorating it must still hand the owner their
        workspace. An empty demonstration is a poor first impression; a failed
        sign-up after the account exists is worse and unrecoverable from the
        screen.
        """
        written: dict[str, list[int]] = {}

        try:
            self._seed_conversations(company_id, written)
            self._seed_catalogue(company_id, owner_user_id, written)
            self._seed_knowledge(company_id, owner_user_id, written)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not finish seeding the demonstration for company %s; "
                "the workspace is usable but will look emptier than intended",
                company_id,
            )
        finally:
            # In `finally`, and this is the whole point of the ledger. Recording
            # only after every part succeeded means a failure in the last part
            # loses the record of everything the earlier parts already wrote --
            # and those rows then survive activation for ever, invented
            # customers sitting in a real workspace with nothing able to
            # identify them. A partial seed must still be a fully recorded one.
            try:
                self._record(company_id, written)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Could not record what was seeded for company %s; its "
                    "sample data will survive activation",
                    company_id,
                )

        return {table: len(ids) for table, ids in written.items()}

    # ------------------------------------------------------------- the parts

    def _seed_conversations(self, company_id: int, written: dict) -> None:
        from backend.services.customer_service import customer_service
        from backend.services.message_service import message_service

        for channel, external_id, display_name, turns in _CONVERSATIONS:
            # The contact first, the way `channels/inbound.py` does it. A real
            # inbound message is `upsert_from_channel` and then `save_message`;
            # `save_message` alone leaves `conversations.customer_id` null, so
            # the demonstration would show five conversations and no contacts
            # -- a Customers screen that is empty while the inbox is full,
            # which is not what this platform looks like in use.
            customer = customer_service.upsert_from_channel(
                company_id=company_id,
                channel=channel,
                external_user_id=external_id,
                display_name=display_name,
            )

            if customer.get("id"):
                written.setdefault("customers", []).append(int(customer["id"]))

            for direction, text, sender_type, minutes in turns:
                saved = message_service.save_message(
                    company_id=company_id,
                    channel=channel,
                    external_user_id=external_id,
                    direction="inbound" if direction == "in" else "outbound",
                    text=text,
                    sender_type=sender_type,
                )

                message_id = saved.get("id") or saved.get("message_id")

                if message_id:
                    written.setdefault("messages", []).append(int(message_id))
                    self._backdate_message(int(message_id), minutes, company_id)

            self._record_conversation(company_id, channel, external_id, written)

    def _record_conversation(
        self, company_id: int, channel: str, external_id: str, written: dict
    ) -> None:
        """Note the conversation `save_message` created on the way past.

        It is a seeded row even though this service did not insert it, so it
        goes in the ledger -- otherwise activation would take the messages and
        leave an empty conversation behind for a customer who never existed.
        """
        with database_manager.tenant(company_id) as conn:
            row = conn.execute(
                "SELECT id FROM conversations "
                "WHERE company_id = ? AND channel = ? AND external_user_id = ? LIMIT 1",
                (company_id, channel, external_id),
            ).fetchone()

            if row is not None:
                written.setdefault("conversations", []).append(int(row["id"]))

    def _seed_catalogue(self, company_id: int, owner_user_id: int, written: dict) -> None:
        from backend.services.catalogue_service import catalogue_service

        for product in _PRODUCTS:
            created = catalogue_service.create_product(
                company_id=company_id, data=dict(product)
            )

            if created.get("id"):
                written.setdefault("products", []).append(int(created["id"]))

    def _seed_knowledge(self, company_id: int, owner_user_id: int, written: dict) -> None:
        from backend.services.knowledge_service import knowledge_service

        for item in _KNOWLEDGE:
            created = knowledge_service.create_item(
                company_id=company_id, data=dict(item)
            )

            if created.get("id"):
                written.setdefault("knowledge_items", []).append(int(created["id"]))

    # ----------------------------------------------------------- bookkeeping

    def _backdate_message(self, row_id: int, minutes: int, company_id: int) -> None:
        """Move one seeded message's `created_at` into the past.

        Only messages are backdated -- the demonstration reads as a history,
        and the reporting screens need the spread of times to have anything to
        plot. The table is a literal in the statement rather than an argument
        written into it: an identifier cannot be a bound `?`, so the safe form
        is not to interpolate a name at all.
        """
        with database_manager.tenant(company_id) as conn:
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ? AND company_id = ?",
                (_minutes_ago(minutes), row_id, company_id),
            )
            conn.commit()

    def _record(self, company_id: int, written: dict) -> None:
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            for table, ids in written.items():
                conn.executemany(
                    "INSERT OR IGNORE INTO demo_seeded_rows "
                    "(company_id, table_name, row_id, created_at) VALUES (?, ?, ?, ?)",
                    [(company_id, table, row_id, now) for row_id in ids],
                )

            conn.commit()

    # ---------------------------------------------------------------- unseed

    # Only these. A ledger naming any other table would be a way to delete
    # arbitrary rows through a code path the owner cannot see, so the allowed
    # set is written here rather than trusted from the ledger.
    REMOVABLE = ("messages", "conversations", "customers", "products", "knowledge_items")

    def remove(self, *, company_id: int) -> dict[str, int]:
        """Take away exactly what was seeded, on activation.

        Deletes by recorded id, so anything the owner added while trying the
        platform out is untouched -- including a real conversation on the same
        channel as a sample one.
        """
        removed: dict[str, int] = {}

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                "SELECT table_name, row_id FROM demo_seeded_rows WHERE company_id = ?",
                (company_id,),
            ).fetchall()

            by_table: dict[str, list[int]] = {}

            for row in rows:
                by_table.setdefault(str(row["table_name"]), []).append(int(row["row_id"]))

            # Messages before conversations before customers: a child deleted
            # after its parent is a row pointing at nothing for as long as the
            # transaction is open, and the order costs nothing to get right.
            for table in self.REMOVABLE:
                ids = by_table.get(table)

                if not ids:
                    continue

                placeholders = ", ".join("?" for _ in ids)
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE company_id = ? "
                    f"AND id IN ({placeholders})",
                    (company_id, *ids),
                )
                removed[table] = int(cursor.rowcount)

            conn.execute(
                "DELETE FROM demo_seeded_rows WHERE company_id = ?", (company_id,)
            )
            conn.commit()

        logger.info("Removed the demonstration data for company %s: %s", company_id, removed)

        return removed


demo_seed_service = DemoSeedService()
