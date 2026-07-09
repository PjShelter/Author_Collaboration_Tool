"""SQLite 运行记录。

风险人员主数据在 data/risk_profiles.yaml 中;
SQLite 只记录机器人运行时观察到的群、命中和提醒情况。
schema 与原 botpy 项目保持一致,只是文件路径变成插件本地的 data/bot.db。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import date
from typing import Iterable


SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS bot_groups (
        group_id INTEGER PRIMARY KEY,
        name TEXT,
        first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS member_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        group_id INTEGER NOT NULL,
        user_id INTEGER,
        profile_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alert_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        user_id INTEGER,
        profile_id TEXT,
        alert_type TEXT NOT NULL,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_download_usage (
        group_id INTEGER NOT NULL,
        usage_date TEXT NOT NULL,
        used_bytes INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (group_id, usage_date)
    )
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    """打开数据库连接,确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path) -> None:
    """创建当前版本需要的表。"""
    with connect(path) as conn:
        for statement in SCHEMA:
            conn.execute(statement)


def record_group(db_path: Path, group_id: int, name: str | None = None) -> None:
    """记录机器人见过的群。"""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bot_groups(group_id, name) VALUES(?, ?) "
            "ON CONFLICT(group_id) DO UPDATE SET "
            "name = COALESCE(excluded.name, bot_groups.name), "
            "last_seen_at = CURRENT_TIMESTAMP",
            (group_id, name),
        )


def record_member_event(
    db_path: Path,
    event_type: str,
    group_id: int,
    user_id: int | None,
    profile_id: str | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO member_events(event_type, group_id, user_id, profile_id) "
            "VALUES(?, ?, ?, ?)",
            (event_type, group_id, user_id, profile_id),
        )


def record_alert(
    db_path: Path,
    group_id: int,
    user_id: int | None,
    profile_id: str | None,
    alert_type: str,
    message: str,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO alert_log(group_id, user_id, profile_id, alert_type, message) "
            "VALUES(?, ?, ?, ?, ?)",
            (group_id, user_id, profile_id, alert_type, message),
        )


def list_recent_alerts(db_path: Path, limit: int = 5) -> Iterable[sqlite3.Row]:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT group_id, user_id, profile_id, alert_type, created_at "
            "FROM alert_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def get_download_usage(db_path: Path, group_id: int, usage_date: date | None = None) -> int:
    day = (usage_date or date.today()).isoformat()
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT used_bytes FROM group_download_usage "
            "WHERE group_id = ? AND usage_date = ?",
            (group_id, day),
        ).fetchone()
    return int(row["used_bytes"]) if row else 0


def add_download_usage(
    db_path: Path,
    group_id: int,
    bytes_count: int,
    usage_date: date | None = None,
) -> int:
    day = (usage_date or date.today()).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO group_download_usage(group_id, usage_date, used_bytes) "
            "VALUES(?, ?, ?) "
            "ON CONFLICT(group_id, usage_date) DO UPDATE SET "
            "used_bytes = used_bytes + excluded.used_bytes, "
            "updated_at = CURRENT_TIMESTAMP",
            (group_id, day, int(bytes_count)),
        )
        row = conn.execute(
            "SELECT used_bytes FROM group_download_usage "
            "WHERE group_id = ? AND usage_date = ?",
            (group_id, day),
        ).fetchone()
    return int(row["used_bytes"]) if row else 0
