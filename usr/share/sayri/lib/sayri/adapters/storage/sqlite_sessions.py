"""SQLite Storage Adapter for Sayri Sessions, Messages, and Gateway Authorizations."""

from dataclasses import asdict, is_dataclass
import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from sayri import paths
from sayri.domain.models import Message, Session, ToolCall, ToolCallStatus


class SQLiteSessionRepository:
    """Manages persistent chat history, sessions, and security authorizations in SQLite."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or paths.sessions_db()
        dname = os.path.dirname(self.db_path)
        if dname:
            os.makedirs(dname, exist_ok=True)
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
            conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                command TEXT NOT NULL,
                rejected_command TEXT DEFAULT '',
                success_count INTEGER DEFAULT 1,
                failure_count INTEGER DEFAULT 0,
                score REAL DEFAULT 1.0,
                notes TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
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

    def list_sessions(self, limit: int = 50, include_empty: bool = False) -> List[Session]:
        with self._get_conn() as conn:
            query = """
                SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as msg_count
                FROM sessions s
            """
            if not include_empty:
                query += " WHERE (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) > 0"
            query += " ORDER BY s.updated_at DESC LIMIT ?"

            rows = conn.execute(query, (limit,)).fetchall()

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
                cnt = r["msg_count"] or 0
                s.messages = [Message(role="user", content="")] * cnt
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
                tool_calls = []
                for tc in tc_data:
                    status_str = tc.get("status", "success")
                    try:
                        status_enum = ToolCallStatus(status_str)
                    except ValueError:
                        status_enum = ToolCallStatus.SUCCESS
                    tool_calls.append(
                        ToolCall(
                            name=tc.get("name", "bash"),
                            arguments=tc.get("arguments", {}),
                            id=tc.get("id", ""),
                            status=status_enum,
                            output=tc.get("output"),
                            exit_code=tc.get("exit_code"),
                            duration_ms=tc.get("duration_ms", 0.0),
                        )
                    )
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

    @staticmethod
    def _serialize_tool_call(tc: Any) -> Dict[str, Any]:
        if isinstance(tc, ToolCall):
            return {
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
                "status": tc.status.value if isinstance(tc.status, ToolCallStatus) else str(tc.status),
                "output": tc.output,
                "exit_code": tc.exit_code,
                "duration_ms": tc.duration_ms,
            }
        elif isinstance(tc, dict):
            return tc
        elif is_dataclass(tc):
            d = asdict(tc)
            if "status" in d and hasattr(d["status"], "value"):
                d["status"] = d["status"].value
            return d
        return {"name": str(tc)}

    def add_message(self, session_id: str, message: Message) -> None:
        now = time.time()
        tc_json = json.dumps([self._serialize_tool_call(tc) for tc in message.tool_calls])
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

    def search_all_messages(self, query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        """Searches messages across ALL sessions (local desktop, telegram, discord gateways)."""
        with self._get_conn() as conn:
            if query.strip():
                rows = conn.execute(
                    """
                    SELECT m.role, m.content, m.timestamp, s.title as session_title, s.id as session_id
                    FROM messages m
                    JOIN sessions s ON m.session_id = s.id
                    WHERE m.content LIKE ? OR s.title LIKE ?
                    ORDER BY m.timestamp DESC LIMIT ?
                    """,
                    (f"%{query.strip()}%", f"%{query.strip()}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT m.role, m.content, m.timestamp, s.title as session_title, s.id as session_id
                    FROM messages m
                    JOIN sessions s ON m.session_id = s.id
                    ORDER BY m.timestamp DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        results = []
        for r in reversed(rows):
            results.append({
                "role": r["role"],
                "content": r["content"][:400],
                "timestamp": r["timestamp"],
                "session_title": r["session_title"],
                "session_id": r["session_id"],
            })
        return results

    # ── Reinforcement & Preference Learning
    def record_preference(
        self,
        agent_id: str,
        intent: str,
        command: str,
        success: bool = True,
        rejected_command: Optional[str] = None,
        notes: str = "",
    ) -> int:
        """Records or updates a learned command preference trajectory."""
        now = time.time()
        with self._get_conn() as conn:
            # Check if matching record exists for intent/command
            existing = conn.execute(
                """
                SELECT id, success_count, failure_count, rejected_command
                FROM agent_preferences
                WHERE agent_id = ? AND intent = ? AND command = ?
                """,
                (agent_id, intent.strip(), command.strip()),
            ).fetchone()

            if existing:
                s_count = existing["success_count"] + (1 if success else 0)
                f_count = existing["failure_count"] + (0 if success else 1)
                score = round(s_count - (f_count * 1.5), 2)
                rej = rejected_command or existing["rejected_command"] or ""
                conn.execute(
                    """
                    UPDATE agent_preferences
                    SET success_count = ?, failure_count = ?, score = ?, rejected_command = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (s_count, f_count, score, rej, notes, now, existing["id"]),
                )
                conn.commit()
                return existing["id"]
            else:
                s_count = 1 if success else 0
                f_count = 0 if success else 1
                score = 1.0 if success else -1.5
                cur = conn.execute(
                    """
                    INSERT INTO agent_preferences
                    (agent_id, intent, command, rejected_command, success_count, failure_count, score, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (agent_id, intent.strip(), command.strip(), rejected_command or "", s_count, f_count, score, notes, now, now),
                )
                conn.commit()
                return cur.lastrowid

    def query_preferences(
        self,
        query: str,
        agent_id: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Queries learned preferences by intent or command keywords."""
        tokens = [t.strip() for t in query.lower().split() if len(t.strip()) > 1]
        with self._get_conn() as conn:
            if not tokens:
                if agent_id:
                    rows = conn.execute(
                        "SELECT * FROM agent_preferences WHERE agent_id = ? ORDER BY score DESC, updated_at DESC LIMIT ?",
                        (agent_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM agent_preferences ORDER BY score DESC, updated_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            else:
                # Build SQL matching any of the tokens
                clauses = []
                params: List[Any] = []
                for tok in tokens:
                    clauses.append("(LOWER(intent) LIKE ? OR LOWER(command) LIKE ? OR LOWER(notes) LIKE ?)")
                    like_tok = f"%{tok}%"
                    params.extend([like_tok, like_tok, like_tok])

                where_str = " OR ".join(clauses)
                if agent_id:
                    where_str = f"agent_id = ? AND ({where_str})"
                    params.insert(0, agent_id)

                sql = f"SELECT * FROM agent_preferences WHERE ({where_str}) AND score >= 0 ORDER BY score DESC, updated_at DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, tuple(params)).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "agent_id": r["agent_id"],
                "intent": r["intent"],
                "command": r["command"],
                "rejected_command": r["rejected_command"],
                "success_count": r["success_count"],
                "failure_count": r["failure_count"],
                "score": r["score"],
                "notes": r["notes"],
                "updated_at": r["updated_at"],
            })
        return results

    def list_preferences(self, agent_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Lists all recorded preferences."""
        with self._get_conn() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM agent_preferences WHERE agent_id = ? ORDER BY score DESC, updated_at DESC LIMIT ?",
                    (agent_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_preferences ORDER BY score DESC, updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_preference(self, pref_id: int) -> bool:
        """Deletes a specific preference by ID."""
        with self._get_conn() as conn:
            res = conn.execute("DELETE FROM agent_preferences WHERE id = ?", (pref_id,))
            conn.commit()
            return res.rowcount > 0

    def clear_preferences(self, agent_id: Optional[str] = None) -> int:
        """Clears all learned preferences for an agent or globally."""
        with self._get_conn() as conn:
            if agent_id:
                res = conn.execute("DELETE FROM agent_preferences WHERE agent_id = ?", (agent_id,))
            else:
                res = conn.execute("DELETE FROM agent_preferences")
            conn.commit()
            return res.rowcount


