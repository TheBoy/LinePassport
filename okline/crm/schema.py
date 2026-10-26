"""CRM tables. Every row carries ``owner_id``: the shop owner (tenant) it belongs to.

Statements must run unchanged on SQLite and PostgreSQL, so they stick to TEXT,
BIGINT and INTEGER columns and keep JSON as TEXT.
"""

from __future__ import annotations

MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """CREATE TABLE IF NOT EXISTS crm_customers (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                fields TEXT NOT NULL DEFAULT '{}',
                picture TEXT NOT NULL DEFAULT '',
                search TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                last_inbound_at BIGINT NOT NULL DEFAULT 0,
                last_outbound_at BIGINT NOT NULL DEFAULT 0,
                last_message_at BIGINT NOT NULL DEFAULT 0,
                last_preview TEXT NOT NULL DEFAULT ''
            )""",
            "CREATE INDEX IF NOT EXISTS crm_customers_recent "
            "ON crm_customers (owner_id, last_message_at)",
            """CREATE TABLE IF NOT EXISTS crm_identities (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                external_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                picture TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE (owner_id, channel, external_id)
            )""",
            "CREATE INDEX IF NOT EXISTS crm_identities_customer ON crm_identities (customer_id)",
            """CREATE TABLE IF NOT EXISTS crm_conversations (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                account_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                assigned_to TEXT NOT NULL DEFAULT '',
                assigned_at BIGINT NOT NULL DEFAULT 0,
                unread INTEGER NOT NULL DEFAULT 0,
                last_message_at BIGINT NOT NULL DEFAULT 0,
                last_inbound_at BIGINT NOT NULL DEFAULT 0,
                last_outbound_at BIGINT NOT NULL DEFAULT 0,
                last_preview TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                closed_at BIGINT NOT NULL DEFAULT 0,
                UNIQUE (account_id, chat_id)
            )""",
            "CREATE INDEX IF NOT EXISTS crm_conversations_customer "
            "ON crm_conversations (customer_id)",
            "CREATE INDEX IF NOT EXISTS crm_conversations_owner_status "
            "ON crm_conversations (owner_id, status)",
            """CREATE TABLE IF NOT EXISTS crm_messages (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                sender_kind TEXT NOT NULL DEFAULT '',
                sender_id TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                content_type INTEGER NOT NULL DEFAULT 0,
                meta TEXT NOT NULL DEFAULT '{}',
                at BIGINT NOT NULL,
                UNIQUE (conversation_id, external_id)
            )""",
            "CREATE INDEX IF NOT EXISTS crm_messages_conversation_at "
            "ON crm_messages (conversation_id, at)",
            """CREATE TABLE IF NOT EXISTS crm_tags (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                UNIQUE (owner_id, name_key)
            )""",
            """CREATE TABLE IF NOT EXISTS crm_customer_tags (
                customer_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                PRIMARY KEY (customer_id, tag_id)
            )""",
            "CREATE INDEX IF NOT EXISTS crm_customer_tags_tag ON crm_customer_tags (tag_id)",
            """CREATE TABLE IF NOT EXISTS crm_notes (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                author_id TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS crm_notes_customer ON crm_notes (customer_id)",
            """CREATE TABLE IF NOT EXISTS crm_tasks (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                customer_id TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                due_at BIGINT NOT NULL DEFAULT 0,
                assigned_to TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                done_at BIGINT NOT NULL DEFAULT 0,
                reminded_at BIGINT NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX IF NOT EXISTS crm_tasks_owner_due ON crm_tasks (owner_id, status, due_at)",
            "CREATE INDEX IF NOT EXISTS crm_tasks_customer ON crm_tasks (customer_id)",
            """CREATE TABLE IF NOT EXISTS crm_events (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                customer_id TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                at BIGINT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS crm_events_customer_at ON crm_events (customer_id, at)",
            """CREATE TABLE IF NOT EXISTS crm_segments (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                filter_json TEXT NOT NULL DEFAULT '{}',
                created_by TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS crm_broadcasts (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                segment_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                account_id TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_by TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                started_at BIGINT NOT NULL DEFAULT 0,
                finished_at BIGINT NOT NULL DEFAULT 0,
                next_at BIGINT NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS crm_broadcast_recipients (
                broadcast_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '',
                chat_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                sent_at BIGINT NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (broadcast_id, customer_id)
            )""",
            "CREATE INDEX IF NOT EXISTS crm_broadcast_recipients_status "
            "ON crm_broadcast_recipients (broadcast_id, status, position)",
            """CREATE TABLE IF NOT EXISTS crm_settings (
                owner_id TEXT PRIMARY KEY,
                data TEXT NOT NULL DEFAULT '{}',
                updated_at BIGINT NOT NULL
            )""",
        ],
    ),
]
