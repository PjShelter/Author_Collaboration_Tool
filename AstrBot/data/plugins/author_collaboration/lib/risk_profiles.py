"""仓库内风险人员档案。

主数据保存在 data/risk_profiles.yaml。

迁移自原 botpy 项目,关键变化:
  - group_openid (字符串) → group_id (int, OneBot v11 数字群号)
  - member_openid (字符串) → user_id (int, OneBot v11 数字 QQ 号)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RiskMatch:
    person_id: str
    display_name: str
    risk_level: str
    reason: str
    note: str


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"admins": [], "trusted_groups": [], "profiles": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("admins", [])
    data.setdefault("trusted_groups", [])
    data.setdefault("profiles", [])
    return data


def _save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _coerce_int(value: Any) -> int | None:
    """YAML 里占位 0 要能容忍;真实配置必须是 int。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_admin(path: Path, group_id: int, user_id: int | None) -> bool:
    if user_id is None:
        return False
    for admin in _load(path).get("admins", []):
        if _coerce_int(admin.get("user_id")) != user_id:
            continue
        admin_group = _coerce_int(admin.get("group_id"))
        if admin_group is None:
            continue
        if admin_group == group_id or admin_group < 0:
            return True
    return False


def is_trusted_group(path: Path, group_id: int) -> bool:
    for item in _load(path).get("trusted_groups", []):
        gid = _coerce_int(item.get("group_id"))
        if gid is None:
            continue
        if gid == group_id or gid < 0:
            return True
    return False


def list_profiles(path: Path) -> list[dict[str, Any]]:
    return list(_load(path).get("profiles", []))


def find_profile(path: Path, person_id: str) -> dict[str, Any] | None:
    for profile in list_profiles(path):
        if profile.get("person_id") == person_id:
            return profile
    return None


def find_match(path: Path, group_id: int, user_id: int | None) -> RiskMatch | None:
    if user_id is None:
        return None
    for profile in list_profiles(path):
        for mapped in profile.get("mapped_members", []) or []:
            mg = _coerce_int(mapped.get("group_id"))
            mu = _coerce_int(mapped.get("user_id"))
            if mu != user_id:
                continue
            if mg == group_id or (mg is not None and mg < 0):
                return RiskMatch(
                    person_id=str(profile.get("person_id", "")),
                    display_name=str(profile.get("display_name", "")),
                    risk_level=str(profile.get("risk_level", "")),
                    reason=str(profile.get("reason", "")),
                    note=str(mapped.get("note", "")),
                )
    return None


def bind_member(
    path: Path,
    person_id: str,
    group_id: int,
    user_id: int,
    note: str = "",
) -> bool:
    data = _load(path)
    for profile in data.get("profiles", []):
        if profile.get("person_id") != person_id:
            continue
        mapped_members = profile.setdefault("mapped_members", [])
        for mapped in mapped_members:
            if (
                _coerce_int(mapped.get("group_id")) == group_id
                and _coerce_int(mapped.get("user_id")) == user_id
            ):
                mapped["note"] = note or mapped.get("note", "")
                _save(data, path)
                return True
        mapped_members.append(
            {"group_id": group_id, "user_id": user_id, "note": note}
        )
        _save(data, path)
        return True
    return False


def unbind_member(path: Path, person_id: str, group_id: int, user_id: int) -> bool:
    data = _load(path)
    for profile in data.get("profiles", []):
        if profile.get("person_id") != person_id:
            continue
        before = list(profile.get("mapped_members", []) or [])
        after = [
            mapped
            for mapped in before
            if not (
                _coerce_int(mapped.get("group_id")) == group_id
                and _coerce_int(mapped.get("user_id")) == user_id
            )
        ]
        profile["mapped_members"] = after
        if len(after) != len(before):
            _save(data, path)
            return True
        return False
    return False


def summary(path: Path) -> tuple[int, int, int]:
    data = _load(path)
    profiles = data.get("profiles", [])
    mappings = sum(len(profile.get("mapped_members", []) or []) for profile in profiles)
    return len(data.get("admins", [])), len(profiles), mappings
