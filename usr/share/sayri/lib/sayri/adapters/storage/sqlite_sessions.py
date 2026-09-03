"""SQLite Storage Adapter for Sayri Sessions, Messages, and Gateway Authorizations."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from sayri import paths
from sayri.domain.models import Message, Session, ToolCall, ToolCallStatus


class SQLiteSessionRepository:
    """Manages persistent chat history, sessions, and security authorizations in SQLite."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or paths.sessions_db()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                token_usage INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls_json TEXT DEFAULT '[]',
                tool_call_id TEXT,
                timestamp REAL NOT NULL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS gateway_peers (
                peer_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                is_authorized INTEGER DEFAULT 0,
                authorized_at REAL,
                metadata TEXT DEFAULT '{}'
            );
            """)
            conn.commit()

    # ── Sessions
    def create_session(
        self,
        agent_id: str = "default",
        title: str = "New Conversation",
        session_id: Optional[str] = None,
    ) -> Session:
        sid = session_id or str(uuid.uuid4())
        session = Session(id=sid, agent_id=agent_id, title=title)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, title, agent_id, created_at, updated_at, token_usage, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    agent_id=excluded.agent_id,
                    updated_at=excluded.updated_at
                """,
                (
                    session.id,
                    session.title,
                    session.agent_id,
                    session.created_at,
                    session.updated_at,
                    session.token_usage,
                    json.dumps(session.metadata),
                ),
            )
            conn.commit()
        return session

    def list_sessions(self, limit: int = 50) -> List[Session]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()

            sessions = []
            for r in rows:
                s = Session(
                    id=r["id"],
                    title=r["title"],
                    agent_id=r["agent_id"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    token_usage=r["token_usage"],
                    metadata=json.loads(r["metadata"] or "{}"),
                )
                sessions.append(s)
            return sessions

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None

            s = Session(
                id=row["id"],
                title=row["title"],
                agent_id=row["agent_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                token_usage=row["token_usage"],
                metadata=json.loads(row["metadata"] or "{}"),
            )

            # Fetch messages
            m_rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            for mr in m_rows:
                tc_data = json.loads(mr["tool_calls_json"] or "[]")
                tool_calls = [
                    ToolCall(
                        name=tc["name"],
                        arguments=tc.get("arguments", {}),
                        id=tc.get("id"),
                        status=ToolCallStatus(tc.get("status", "success")),
                        output=tc.get("output"),
                        exit_code=tc.get("exit_code"),
                        duration_ms=tc.get("duration_ms", 0.0),
                    )
                    for tc in tc_data
                ]
                msg = Message(
                    id=str(mr["id"]),
                    role=mr["role"],
                    content=mr["content"],
                    tool_calls=tool_calls,
                    tool_call_id=mr["tool_call_id"],
                    timestamp=mr["timestamp"],
                    metadata=json.loads(mr["metadata"] or "{}"),
                )
                s.messages.append(msg)

            return s

    def add_message(self, session_id: str, message: Message) -> None:
        now = time.time()
        tc_json = json.dumps([asdict(tc) for tc in message.tool_calls])
        with self._get_conn() as conn:
            # Ensure session exists in sessions table to maintain referential integrity
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions (id, title, agent_id, created_at, updated_at, token_usage, metadata)
                VALUES (?, ?, ?, ?, ?, 0, '{}')
                """,
                (session_id, message.content[:30] or "Conversation", "default", now, now),
            )
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, tool_calls_json, tool_call_id, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    message.role,
                    message.content,
                    tc_json,
                    message.tool_call_id,
                    message.timestamp or now,
                    json.dumps(message.metadata),
                ),
            )
            # Update session timestamp
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

    def update_session_title(self, session_id: str, title: str) -> None:
        with self._get_conn() as conn:
            conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
            conn.commit()

    def delete_session(self, session_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    # ── Gateway Token Shield & Authorizations
    def is_gateway_peer_authorized(self, peer_id: str, channel: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT is_authorized FROM gateway_peers WHERE peer_id = ? AND channel = ?",
                (peer_id, channel),
            ).fetchone()
            if row:
                return bool(row["is_authorized"])
        return False

    def authorize_gateway_peer(self, peer_id: str, channel: str, agent_id: str = "default") -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO gateway_peers (peer_id, channel, agent_id, is_authorized, authorized_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(peer_id) DO UPDATE SET is_authorized = 1, authorized_at = ?
                """,
                (peer_id, channel, agent_id, time.time(), time.time()),
            )
            conn.commit()

    def revoke_gateway_peer(self, peer_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM gateway_peers WHERE peer_id = ?", (peer_id,))
            conn.commit()

    # ── History & Smart Context Retrieval
    def get_last_session_for_prefix(self, prefix: str) -> Optional[Session]:
        """Finds the most recently updated session starting with a given prefix."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE id LIKE ? ORDER BY updated_at DESC LIMIT 1",
                (f"{prefix}%",),
            ).fetchone()
            if row:
                return self.get_session(row["id"])
        return None

    def search_session_messages(self, session_id: str, query: str = "", limit: int = 8) -> List[Dict[str, Any]]:
        """Searches messages in a session or retrieves past messages formatted cleanly for memory recall."""
        with self._get_conn() as conn:
            if query.strip():
                rows = conn.execute(
                    """
                    SELECT role, content, timestamp FROM messages
                    WHERE session_id = ? AND content LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (session_id, f"%{query.strip()}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT role, content, timestamp FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()

        results = []
        for r in reversed(rows):
            results.append({
                "role": r["role"],
                "content": r["content"][:400],
                "timestamp": r["timestamp"],
            })
        return results

