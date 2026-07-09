"""黑名单查询门面。

包装 risk_profiles,对外暴露 is_blacklisted / get_blacklist_match / format_blacklist。
"""
from __future__ import annotations

from pathlib import Path

from . import risk_profiles


def is_blacklisted(path: Path, group_id: int, user_id: int | None) -> bool:
    return risk_profiles.find_match(path, group_id, user_id) is not None


def get_blacklist_match(
    path: Path, group_id: int, user_id: int | None
) -> risk_profiles.RiskMatch | None:
    return risk_profiles.find_match(path, group_id, user_id)


def format_blacklist(path: Path, limit: int = 10) -> str:
    profiles = risk_profiles.list_profiles(path)
    if not profiles:
        return "当前黑名单为空。"
    lines = ["当前黑名单:"]
    for profile in profiles[:limit]:
        aliases = "、".join(profile.get("aliases", []) or [])
        name = str(profile.get("display_name") or profile.get("person_id") or "未命名")
        qq_number = str(profile.get("qq_number") or "未记录")
        reason = str(profile.get("reason") or "暂无公开原因")
        if aliases:
            lines.append(
                f"- {name} / QQ: {qq_number} / 曾用名: {aliases}\n  原因: {reason}"
            )
        else:
            lines.append(f"- {name} / QQ: {qq_number}\n  原因: {reason}")
    if len(profiles) > limit:
        lines.append(f"... 还有 {len(profiles) - limit} 条")
    return "\n".join(lines)
