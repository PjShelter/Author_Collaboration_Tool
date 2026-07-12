"""插件配置/路径辅助。

AstrBot 已经通过 context.get_config() 把 _conf_schema.json 渲染成 dict 传给我们;
本模块只负责解析插件本地 data 目录、补充默认值、提供路径解析。

插件文件布局:
    plugins/author_collaboration/
        metadata.yaml
        main.py
        _conf_schema.json
        lib/
            config_helper.py    <- 本文件
            ...
        data/
            risk_profiles.yaml  <- 风险人员主数据
            bot.db              <- SQLite 运行记录 (gitignored)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


PLUGIN_ROOT: Path = Path(__file__).resolve().parent.parent
PLUGIN_DATA_DIR: Path = PLUGIN_ROOT / "data"


def data_path(rel: str) -> Path:
    """相对插件 data/ 目录的路径,自动创建父目录。"""
    p = (PLUGIN_DATA_DIR / rel).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_DEFAULTS: dict[str, Any] = {
    "admin_qqs": [],
    "trusted_group_ids": [],
    "kick_on_match": True,
    "risk_profiles_path": "data/risk_profiles.yaml",
    "bot_db_path": "data/bot.db",
    "act_group_number": "621930922",
    "max_text_len": 1800,
    "meme_api_base": "http://meme-generator:2233",
    "meme_enabled": True,
    # Template keys to suppress from the meme-generator service.
    # Add the meme's template key (e.g. "my_template_key") to hide it from
    # keyword lookup and the /memes listing.  Find keys with:
    #   docker compose exec meme-generator curl -s http://localhost:2233/memes/keys
    "meme_blocked_keys": [],
}


def merge_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """把 AstrBot 传来的 config 与默认配置合并,缺失字段补默认值。"""
    out: dict[str, Any] = dict(_DEFAULTS)
    if raw:
        for k, v in raw.items():
            if v is None or v == "":
                continue
            out[k] = v
    return out


def resolve_risk_profiles_path(cfg: dict[str, Any]) -> Path:
    """把 risk_profiles_path 解析成绝对路径,优先相对插件根目录。"""
    rel = str(cfg.get("risk_profiles_path", "data/risk_profiles.yaml"))
    p = Path(rel)
    if not p.is_absolute():
        p = (PLUGIN_ROOT / rel).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_bot_db_path(cfg: dict[str, Any]) -> Path:
    """把 bot_db_path 解析成绝对路径,优先相对插件根目录。"""
    rel = str(cfg.get("bot_db_path", "data/bot.db"))
    p = Path(rel)
    if not p.is_absolute():
        p = (PLUGIN_ROOT / rel).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
