import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing


class ChatHistory:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(id)"
                )

    def add(self, role: str, content: str) -> None:
        if not content:
            return
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()

    def latest(self, limit: int) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
                (max(0, limit),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def older(self, before_id: int, limit: int) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE id < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (before_id, max(0, limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def search(self, query: str, limit: int = 100) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (f"%{query}%", max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_prompts(self, limit: int = 100) -> list[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        prompts = []
        for row in rows:
            prompt = row["content"]
            if prompt not in prompts:
                prompts.append(prompt)
        return prompts

    def clear(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM messages")
            connection.commit()

    def prune(self, maximum: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                DELETE FROM messages
                WHERE id NOT IN (
                    SELECT id FROM messages ORDER BY id DESC LIMIT ?
                )
                """,
                (max(0, maximum),),
            )
            connection.commit()
