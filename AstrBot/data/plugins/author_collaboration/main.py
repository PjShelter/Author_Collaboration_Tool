"""作者协作黑名单 - AstrBot 插件主入口。

迁移自 Author_Collaboration_Tool/src/ (botpy 版),把 botpy 的 Client 子类
改成 Star 子类;四个事件入口都换成 AstrBot v4.23.5 的 @filter 装饰器。

AstrBot v4.23.5 API 实际位置(本文件实测):
  - filter 装饰器来自  astrbot.api.event.filter  (不是 astrbot.api.filter)
  - 命令:                @filter.command(name, alias={...})
  - 平台过滤:            @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
  - 入群事件:            走 @filter.custom_filter + 自定义 CustomFilter 判 raw_message
  - 群/用户标识:         event.message_obj.raw_message / event.get_group_id()
  - MessageChain:        astrbot.api.event.MessageChain
  - Plain / At:          astrbot.api.message_components

行为对齐原 botpy 版:
  - /查询黑名单       -> 列出 data/risk_profiles.yaml 里的风险人员
  - /加入ACT作者互助会 -> 回复互助会群号
  - /寻求帮助          -> 安慰 + 互助会群号
  - 群成员入群事件     -> 命中风险档案先尝试踢,踢失败降级为 @
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from sys import maxsize

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import CustomFilter, on_astrbot_loaded
from astrbot.api.message_components import At, File, Image, Plain
from astrbot.core import file_token_service

from .lib import blacklist, db, live2d_api, meme_api, notify, risk_profiles
from .lib.config_helper import merge_config, resolve_bot_db_path, resolve_risk_profiles_path


_log = logging.getLogger("author_collaboration")

DEFAULT_ACT_GROUP = "621930922"

COMMAND_BLACKLIST = "查询黑名单"
COMMAND_BLACKLIST_ALIASES = {"黑名单", "名单查询", "1"}
COMMAND_JOIN_ACT = "加入ACT作者互助会"
COMMAND_JOIN_ACT_ALIASES = {"加入互助", "加入作者互助会", "2"}
COMMAND_HELP = "寻求帮助"
COMMAND_HELP_ALIASES = {"求助", "我要求助", "3"}
COMMAND_CARD_QUERY = "卡面查询"
COMMAND_CARD_QUERY_ALIASES = {"查卡面", "卡面", "4"}
COMMAND_CHIBI_DOWNLOAD = "小人下载"
COMMAND_CHIBI_DOWNLOAD_ALIASES = {"下载小人", "小人", "5"}
COMMAND_LIVE2D_DOWNLOAD = "live2d下载"
COMMAND_LIVE2D_DOWNLOAD_ALIASES = {"Live2D下载", "模型下载", "下载live2d", "6"}
COMMAND_MEME_HELP = "meme功能"
COMMAND_MEME_HELP_ALIASES = {"表情包", "meme", "7"}
COMMAND_MEME_LIST = "memes"
COMMAND_MEME_LIST_ALIASES = {"表情列表", "模板列表", "memelist"}
COMMAND_MENU = "帮助"
COMMAND_MENU_ALIASES = {"菜单", "指令", "指令列表", "help", "0"}

KNOWN_COMMANDS = {
    COMMAND_BLACKLIST,
    *COMMAND_BLACKLIST_ALIASES,
    COMMAND_JOIN_ACT,
    *COMMAND_JOIN_ACT_ALIASES,
    COMMAND_HELP,
    *COMMAND_HELP_ALIASES,
    COMMAND_CARD_QUERY,
    *COMMAND_CARD_QUERY_ALIASES,
    COMMAND_LIVE2D_DOWNLOAD,
    *COMMAND_LIVE2D_DOWNLOAD_ALIASES,
    COMMAND_MEME_HELP,
    *COMMAND_MEME_HELP_ALIASES,
    COMMAND_MEME_LIST,
    *COMMAND_MEME_LIST_ALIASES,
    COMMAND_MENU,
    *COMMAND_MENU_ALIASES,
}

LIVE2D_COMMANDS = {
    COMMAND_CARD_QUERY,
    *COMMAND_CARD_QUERY_ALIASES,
    COMMAND_LIVE2D_DOWNLOAD,
    *COMMAND_LIVE2D_DOWNLOAD_ALIASES,
}

DEFAULT_DAILY_DOWNLOAD_LIMIT_BYTES = 100 * 1024 * 1024
CARD_QUERY_PAGE_SIZE = 5
LIVE2D_MODEL_PAGE_SIZE = 5
MEME_LIST_PAGE_SIZE = 30
INTERNAL_FILE_BASE_URL = "http://astrbot:6185/api/file"

DEFAULT_MEME_ALIASES: dict[str, str] = {
    "狗耳帽": "dog_ear_hat",
    "狗帽": "dog_ear_hat",
    "摸摸": "petpet",
    "摸头": "petpet",
    "猫舔": "cat_lick",
    "猫猫舔": "cat_lick",
    "猫抓": "cat_scratch",
    "猫猫抓": "cat_scratch",
    "捶你": "chuini",
    "doro点赞": "doro_thumbs_up",
    "Doro点赞": "doro_thumbs_up",
    "桃乐丝点赞": "doro_thumbs_up",
}


class GroupIncreaseFilter(CustomFilter):
    """只放行 OneBot v11 的群成员入群通知。

    aiocqhttp 平台适配器会把 OneBot notice 事件转成 AstrBotMessage,
    原事件字典保留在 message_obj.raw_message。
    我们用 post_type=notice + notice_type=group_increase 判断。
    """

    def filter(self, event: AstrMessageEvent, cfg) -> bool:  # type: ignore[override]
        raw = getattr(event.message_obj, "raw_message", None)
        if not raw:
            return False
        try:
            return (
                raw.get("post_type") == "notice"
                and raw.get("notice_type") == "group_increase"
            )
        except AttributeError:
            return False

    def __call__(self, raise_error: bool = True) -> "GroupIncreaseFilter":
        """兼容 register_custom_filter 装饰器的调用约定 (instance(raise_error))."""
        self.raise_error = raise_error
        return self


class AuthorCollaborationPlugin(star.Star):
    """AstrBot Star 插件。"""

    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        raw_cfg = {}
        try:
            raw_cfg = context.get_config() or {}
        except Exception as e:
            _log.warning(f"读取插件配置失败,使用默认值: {e}")

        self.cfg = merge_config(raw_cfg)
        self.risk_path = resolve_risk_profiles_path(self.cfg)
        self.db_path = resolve_bot_db_path(self.cfg)
        db.init_db(self.db_path)

        self.act_group = str(self.cfg.get("act_group_number") or DEFAULT_ACT_GROUP)
        self.kick_on_match = bool(self.cfg.get("kick_on_match", True))
        self.trusted_groups = list(self.cfg.get("trusted_group_ids") or [])
        self.admin_qqs = {
            int(q) for q in (self.cfg.get("admin_qqs") or []) if str(q).strip()
        }
        self.max_text_len = int(self.cfg.get("max_text_len", 1800))
        self.daily_download_limit_bytes = int(
            self.cfg.get("daily_download_limit_bytes")
            or DEFAULT_DAILY_DOWNLOAD_LIMIT_BYTES
        )
        self.live2d = live2d_api.ShelterLive2DClient()
        self.meme_enabled = bool(self.cfg.get("meme_enabled", True))
        self.meme = meme_api.MemeGeneratorClient(
            str(self.cfg.get("meme_api_base") or meme_api.DEFAULT_BASE_URL)
        )
        self.meme_aliases = dict(DEFAULT_MEME_ALIASES)

        self.scheduler = AsyncIOScheduler()
        try:
            self.scheduler.start()
        except Exception as e:
            _log.warning(f"APScheduler 启动失败(非致命): {e}")

        _log.info(
            f"author_collaboration 已加载: risk_path={self.risk_path} "
            f"db_path={self.db_path} act_group={self.act_group}"
        )

    async def terminate(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            _log.warning(f"APScheduler 关闭失败: {e}")
        _log.info("author_collaboration 已卸载")

    def _format_hit(self, match: risk_profiles.RiskMatch, user_id: int) -> str:
        return notify.join_lines(
            "风险档案命中,请管理员人工处理。",
            f"user_id: {user_id}",
            f"person_id: {match.person_id}",
            f"昵称/标识: {match.display_name}",
            f"等级: {match.risk_level}",
            f"原因: {match.reason}",
        )

    def _command_menu_text(self) -> str:
        return notify.join_lines(
            "可用指令:",
            "/1 或 /查询黑名单 - 查看当前风险人员列表",
            "/2 或 /加入ACT作者互助会 - 获取互助会群号",
            "/3 或 /寻求帮助 - 获取求助提示",
            "/4 或 /卡面查询 关键词 - 查询卡面和图片链接",
            "/6 或 /live2d下载 关键词 - 查询/下载 Live2D 压缩包",
            "/7 或 /meme功能 - 查看 meme 表情触发说明",
            "/memes 或 /表情列表 - 查看可用 meme 关键词表",
            "/0 或 /帮助 - 显示本指令列表",
        )

    def _meme_help_text(self) -> str:
        aliases = "、".join(sorted(self.meme_aliases.keys()))
        return notify.join_lines(
            "meme 表情功能:",
            "直接发送关键词即可触发,不需要 / 开头。",
            "示例: 狗耳帽@某人 / 狗耳帽 自己 / 摸摸@某人",
            "没有 @ 或图片时,默认使用发送者头像。",
            f"当前常用关键词: {aliases}",
            "查看全部关键词表: /memes 或 /表情列表",
        )

    def _meme_aliases(self) -> dict[str, str]:
        """Merged alias dict for trigger matching.

        Starts from the hardcoded DEFAULT_MEME_ALIASES (always available, even
        if meme-generator is down) and overlays the dynamic cache populated by
        meme_api.MemeGeneratorClient.find_by_keyword. Dynamic wins on collision.
        """
        merged: dict[str, str] = dict(DEFAULT_MEME_ALIASES)
        merged.update(self.meme._keyword_index)
        return merged

    def _looks_like_known_command(self, text: str) -> bool:
        normalized = text.strip()
        if normalized.startswith("/"):
            normalized = normalized[1:].strip()
        normalized = normalized.split(maxsplit=1)[0] if normalized else ""
        return normalized in KNOWN_COMMANDS

    def _command_args(self, event: AstrMessageEvent, command_names: set[str]) -> str:
        text = event.get_message_str().strip()
        if text.startswith("/"):
            text = text[1:].strip()
        for name in sorted(command_names, key=len, reverse=True):
            if text == name:
                return ""
            if text.startswith(name):
                return text[len(name) :].strip()
        parts = text.split(maxsplit=1)
        if parts and parts[0] in command_names:
            return parts[1].strip() if len(parts) > 1 else ""
        return ""

    def _parse_paged_query(self, text: str) -> tuple[str, int]:
        query = text.strip()
        page = 1
        patterns = (
            r"\s+p(?:age)?\s*(\d+)$",
            r"\s+第\s*(\d+)\s*页$",
            r"\s+(\d+)\s*页$",
            r"\s+(\d+)$",
            r"^(\d+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, query, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                page = max(1, int(match.group(1)))
            except ValueError:
                page = 1
            query = query[: match.start()].strip()
            break
        return query, page

    def _is_exact_card_query(self, query: str) -> bool:
        return bool(re.fullmatch(r"\d+|res\d+", query.strip(), flags=re.IGNORECASE))

    def _short_text(self, text: str, limit: int = 18) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _package_dir(self) -> Path:
        package_dir = self.db_path.parent / "live2d_packages"
        package_dir.mkdir(parents=True, exist_ok=True)
        return package_dir

    def _group_id_int(self, event: AstrMessageEvent) -> int | None:
        group_id = event.get_group_id()
        if not group_id:
            return None
        try:
            return int(group_id)
        except (TypeError, ValueError):
            return None

    def _download_usage_text(self, group_id: int | None, used_bytes: int | None = None) -> str:
        if group_id is None:
            return "私聊不计入群下载额度。"
        used = (
            db.get_download_usage(self.db_path, group_id)
            if used_bytes is None
            else used_bytes
        )
        return (
            "本群今日下载额度: "
            f"{live2d_api.format_bytes(used)} / "
            f"{live2d_api.format_bytes(self.daily_download_limit_bytes)}"
        )

    async def _consume_download_quota(
        self,
        event: AstrMessageEvent,
        bytes_count: int,
    ) -> tuple[bool, str]:
        group_id = self._group_id_int(event)
        if group_id is None or bytes_count <= 0:
            return True, self._download_usage_text(group_id)
        used = db.get_download_usage(self.db_path, group_id)
        if used + bytes_count > self.daily_download_limit_bytes:
            return (
                False,
                "本群今日下载额度不足: "
                f"已用 {live2d_api.format_bytes(used)}, "
                f"本次需要 {live2d_api.format_bytes(bytes_count)}, "
                f"上限 {live2d_api.format_bytes(self.daily_download_limit_bytes)}。",
            )
        used = db.add_download_usage(self.db_path, group_id, bytes_count)
        return True, self._download_usage_text(group_id, used)

    def _check_download_quota(
        self,
        event: AstrMessageEvent,
        bytes_count: int,
    ) -> tuple[bool, str]:
        group_id = self._group_id_int(event)
        if group_id is None or bytes_count <= 0:
            return True, self._download_usage_text(group_id)
        used = db.get_download_usage(self.db_path, group_id)
        if used + bytes_count > self.daily_download_limit_bytes:
            return (
                False,
                "本群今日下载额度不足: "
                f"已用 {live2d_api.format_bytes(used)}, "
                f"本次需要 {live2d_api.format_bytes(bytes_count)}, "
                f"上限 {live2d_api.format_bytes(self.daily_download_limit_bytes)}。",
            )
        return True, self._download_usage_text(group_id, used)

    def _record_download_usage(
        self,
        event: AstrMessageEvent,
        bytes_count: int,
    ) -> str:
        group_id = self._group_id_int(event)
        if group_id is None or bytes_count <= 0:
            return self._download_usage_text(group_id)
        used = db.add_download_usage(self.db_path, group_id, bytes_count)
        return self._download_usage_text(group_id, used)

    def _format_model_links(
        self,
        model_name: str,
        files: list[tuple[str, str]],
        max_links: int = 8,
    ) -> str:
        build_data_url = (
            f"{live2d_api.ASSETS_BASE}/jp/live2d/chara/"
            f"{model_name}_rip/buildData.asset"
        )
        lines = [
            f"buildData: {build_data_url}",
            f"资源文件数: {len(files)}",
        ]
        for kind, url in files[:max_links]:
            lines.append(f"{kind}: {url}")
        if len(files) > max_links:
            lines.append(f"其余 {len(files) - max_links} 个动作/表情文件请从 buildData.asset 继续下载。")
        return notify.join_lines(*lines)

    async def _send_live2d_zip(
        self,
        event: AstrMessageEvent,
        zip_path: Path,
    ) -> tuple[bool, str]:
        bot = getattr(event, "bot", None)
        group_id = self._group_id_int(event)
        if bot is not None and group_id is not None:
            try:
                token = await file_token_service.register_file(str(zip_path), timeout=600)
                await bot.call_action(
                    "upload_group_file",
                    group_id=group_id,
                    file=f"{INTERNAL_FILE_BASE_URL}/{token}",
                    name=zip_path.name,
                    folder="",
                )
                return True, "已上传群文件。"
            except Exception as e:
                _log.warning(f"upload_group_file 失败,尝试文件消息: {e}")

        if bot is not None and event.is_private_chat():
            try:
                token = await file_token_service.register_file(str(zip_path), timeout=600)
                await bot.call_action(
                    "upload_private_file",
                    user_id=int(event.get_sender_id()),
                    file=f"{INTERNAL_FILE_BASE_URL}/{token}",
                    name=zip_path.name,
                )
                return True, "已上传文件。"
            except Exception as e:
                _log.warning(f"upload_private_file 失败,尝试文件消息: {e}")

        try:
            token = await file_token_service.register_file(str(zip_path), timeout=600)
            await event.send(
                MessageChain(
                    [
                        Plain("Live2D ZIP:"),
                        File(zip_path.name, url=f"{INTERNAL_FILE_BASE_URL}/{token}"),
                    ]
                )
            )
            return True, "已发送文件消息。"
        except Exception as e:
            _log.warning(f"发送文件消息失败: {e}")
            return False, f"文件发送失败: {e}"

    def _mentions_self(self, event: AstrMessageEvent) -> bool:
        self_id = str(event.get_self_id())
        return any(
            isinstance(message, At) and str(message.qq) == self_id
            for message in event.get_messages()
        )

    def _parse_meme_trigger(self, event: AstrMessageEvent) -> tuple[str, str, str] | None:
        text = event.get_message_str().strip()
        if not text or text.startswith("/"):
            return None
        for alias, key in sorted(self._meme_aliases().items(), key=lambda item: len(item[0]), reverse=True):
            if text == alias:
                return key, alias, ""
            if text.startswith(alias):
                rest = text[len(alias) :].strip()
                rest = re.sub(r"^@[^()\s]+(?:\(\d+\))?\s*", "", rest).strip()
                return key, alias, rest
        return None

    async def _parse_meme_trigger_lazy(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, str, str] | None:
        trigger = self._parse_meme_trigger(event)
        if trigger is not None:
            return trigger
        if self.meme._keyword_index:
            return None
        try:
            await self.meme.list_keywords()
        except Exception as e:
            _log.warning(f"meme 关键词懒加载失败: {e}")
            return None
        return self._parse_meme_trigger(event)

    def _meme_image_sources(self, event: AstrMessageEvent, max_images: int | None = None) -> list[str]:
        self_id = str(event.get_self_id())
        urls: list[str] = []
        for message in event.get_messages():
            if isinstance(message, At):
                if str(message.qq) not in {self_id, "all"}:
                    urls.append(meme_api.avatar_url(message.qq))
            elif isinstance(message, Image):
                url = message.url or message.file or ""
                if url.startswith("http://") or url.startswith("https://"):
                    urls.append(url)
        if max_images is not None and max_images <= 0:
            return []
        if not urls:
            urls = [meme_api.avatar_url(event.get_sender_id())]
        if max_images is not None:
            return urls[:max_images]
        return urls

    def _meme_keyword_table_path(self) -> Path:
        return self.db_path.parent / "meme_keyword_table.png"

    def _meme_param_limits(self, info: dict) -> tuple[int, int, int, int, list[str]]:
        params = info.get("params_type") or {}
        min_images = int(params.get("min_images") or 0)
        max_images = int(params.get("max_images") if params.get("max_images") is not None else min_images)
        min_texts = int(params.get("min_texts") or 0)
        max_texts = int(params.get("max_texts") if params.get("max_texts") is not None else min_texts)
        default_texts = [str(text) for text in (params.get("default_texts") or [])]
        return min_images, max_images, min_texts, max_texts, default_texts

    def _split_meme_texts(self, rest: str, max_texts: int) -> list[str]:
        rest = rest.strip()
        if not rest:
            return []
        if max_texts <= 1:
            return [rest]
        if any(sep in rest for sep in ("|", "｜", "\n")):
            return [
                item.strip()
                for item in re.split(r"\s*[|｜\n]\s*", rest)
                if item.strip()
            ]
        return [rest]

    def _meme_text_args(
        self,
        rest: str,
        min_texts: int,
        max_texts: int,
        default_texts: list[str],
    ) -> list[str]:
        texts = self._split_meme_texts(rest, max_texts)
        if not texts and default_texts:
            texts = default_texts[:max_texts]
        if len(texts) == 1 and min_texts > 1 and default_texts:
            filled = default_texts[:max_texts]
            value = texts[0]
            replaced = False
            for idx, default in enumerate(filled):
                if "xxx" in default:
                    filled[idx] = default.replace("xxx", value)
                    replaced = True
                    break
            if not replaced:
                filled[min(len(filled), max_texts) - 1] = value
            texts = filled
        if len(texts) < min_texts and default_texts:
            for default in default_texts[len(texts):max_texts]:
                texts.append(default)
                if len(texts) >= min_texts:
                    break
        if max_texts >= 0:
            texts = texts[:max_texts]
        return texts

    def _meme_usage_hint(
        self,
        alias: str,
        min_images: int,
        max_images: int,
        min_texts: int,
        max_texts: int,
    ) -> str:
        if max_images == 0 and min_texts > 0:
            return f"用法: {alias} 要写入的文字"
        if min_images > 0 and max_texts == 0:
            return f"用法: {alias}@某人 或 {alias} + 图片"
        if min_images > 0 and min_texts > 0:
            return f"用法: {alias}@某人 文字"
        return f"用法: 直接发送 {alias}"

    async def _prepare_meme_payload(
        self,
        event: AstrMessageEvent,
        key: str,
        alias: str,
        rest: str,
    ) -> tuple[list[str], list[str], str | None]:
        info = await self.meme.template_info(key)
        if info is None:
            info = {"key": key, "params_type": {"min_images": 1, "max_images": 1, "min_texts": 0, "max_texts": 0}}
        min_images, max_images, min_texts, max_texts, default_texts = self._meme_param_limits(info)

        image_urls = self._meme_image_sources(event, max_images)
        if len(image_urls) < min_images:
            return [], [], self._meme_usage_hint(alias, min_images, max_images, min_texts, max_texts)

        texts = self._meme_text_args(rest, min_texts, max_texts, default_texts)
        if len(texts) < min_texts:
            return [], [], self._meme_usage_hint(alias, min_images, max_images, min_texts, max_texts)
        return image_urls, texts, None

    async def _send_welcome(self, event: AstrMessageEvent, user_id: int) -> None:
        try:
            await event.send(
                MessageChain(
                    [
                        Plain("欢迎新成员 "),
                        At(qq=user_id),
                        Plain(" 加入本群。请阅读群规,友善交流,遵守社区百合文化共识。"),
                    ]
                )
            )
        except Exception as e:
            _log.warning(f"发送欢迎消息失败: user_id={user_id} err={e}")

    async def _send_group_text(self, bot, group_id: int, text: str) -> None:
        await bot.call_action(
            "send_group_msg",
            group_id=group_id,
            message=[{"type": "text", "data": {"text": text}}],
        )

    async def _scan_group_blacklist(
        self,
        event: AstrMessageEvent,
    ) -> tuple[int | None, list[tuple[int, risk_profiles.RiskMatch]], str | None]:
        group_id_text = event.get_group_id()
        if not group_id_text:
            return None, [], None

        bot = getattr(event, "bot", None)
        if bot is None:
            return None, [], "无法扫描当前群成员: 缺少 OneBot 连接。"

        try:
            group_id = int(group_id_text)
            members = await bot.call_action("get_group_member_list", group_id=group_id)
        except Exception as e:
            _log.warning(f"扫描群成员失败: group_id={group_id_text} err={e}")
            return None, [], f"无法扫描当前群成员: {e}"

        hits: list[tuple[int, risk_profiles.RiskMatch]] = []
        seen_user_ids: set[int] = set()
        for member in members or []:
            try:
                user_id = int(member.get("user_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            match = blacklist.get_blacklist_match(self.risk_path, group_id, user_id)
            if match is not None:
                hits.append((user_id, match))
        return group_id, hits, None

    async def _kick_with_fallback(
        self,
        event: AstrMessageEvent,
        user_id: int,
        group_id: int,
        reject_add_request: bool = False,
    ) -> str:
        bot = getattr(event, "bot", None)
        if bot is None:
            _log.warning("event 上拿不到 bot 实例,无法调用 OneBot API")
            return "no_bot_handle"
        try:
            await bot.call_action(
                "set_group_kick",
                group_id=group_id,
                user_id=user_id,
                reject_add_request=reject_add_request,
            )
            _log.info(f"已踢出黑名单成员 user_id={user_id} group_id={group_id}")
            return "kick_success"
        except Exception as e:
            _log.warning(f"踢人失败,降级为 @ 警告: {e}")
            try:
                await event.send(
                    MessageChain(
                        [
                            At(qq=user_id),
                            Plain(" 你已被本群加入黑名单,请自觉退群。"),
                        ]
                    )
                )
            except Exception as send_err:
                _log.error(f"降级 @ 警告也失败: {send_err}")
                return "kick_failed_warn_failed"
            return "warn_fallback"

    async def _delayed_kick_blacklisted(
        self,
        bot,
        group_id: int,
        user_id: int,
        match: risk_profiles.RiskMatch,
    ) -> None:
        try:
            await bot.call_action(
                "set_group_kick",
                group_id=group_id,
                user_id=user_id,
                reject_add_request=True,
            )
            _log.info(
                f"延迟踢出黑名单成员成功 group_id={group_id} user_id={user_id}"
            )
            db.record_alert(
                self.db_path,
                group_id,
                user_id,
                match.person_id,
                "delayed_kick_success",
                f"延迟 60 秒踢出并拒绝再次加群: {match.reason}",
            )
        except Exception as e:
            _log.warning(
                f"延迟踢出黑名单成员失败 group_id={group_id} user_id={user_id}: {e}"
            )
            db.record_alert(
                self.db_path,
                group_id,
                user_id,
                match.person_id,
                "delayed_kick_failed",
                str(e),
            )
            try:
                await self._send_group_text(
                    bot,
                    group_id,
                    f"黑名单成员 {user_id} 延迟踢出失败,请管理员手动处理: {e}",
                )
            except Exception as send_err:
                _log.error(f"发送延迟踢出失败提示失败: {send_err}")

    async def _quarantine_blacklisted_join(
        self,
        event: AstrMessageEvent,
        group_id: int,
        user_id: int,
        match: risk_profiles.RiskMatch,
    ) -> str:
        bot = getattr(event, "bot", None)
        if bot is None:
            _log.warning("event 上拿不到 bot 实例,无法禁言和延迟踢出")
            return "no_bot_handle"

        risk_profiles.bind_member(
            self.risk_path,
            match.person_id,
            group_id,
            user_id,
            "入群命中后自动加入本群黑名单",
        )

        mute_status = "mute_success"
        try:
            await bot.call_action(
                "set_group_ban",
                group_id=group_id,
                user_id=user_id,
                duration=60,
            )
            _log.info(f"已禁言黑名单入群成员 60 秒 group={group_id} user={user_id}")
        except Exception as e:
            mute_status = "mute_failed"
            _log.warning(f"禁言黑名单入群成员失败 group={group_id} user={user_id}: {e}")

        try:
            await event.send(
                MessageChain(
                    [
                        Plain("欢迎新成员 "),
                        At(qq=user_id),
                        Plain(
                            "\n检测到该成员命中本群黑名单,已进入 60 秒处理流程。\n"
                            f"记录对象: {match.display_name} / QQ: {user_id}\n"
                            f"记录原因: {match.reason}\n"
                            "处理: 已加入本群黑名单,将于 1 分钟后踢出并拒绝再次申请。"
                        ),
                    ]
                )
            )
        except Exception as e:
            _log.warning(f"发送黑名单入群处理消息失败: {e}")

        self.scheduler.add_job(
            self._delayed_kick_blacklisted,
            "date",
            run_date=datetime.now() + timedelta(seconds=60),
            args=[bot, group_id, user_id, match],
            id=f"delayed_kick_{group_id}_{user_id}_{int(datetime.now().timestamp())}",
            replace_existing=True,
        )
        return f"{mute_status}_delayed_kick_scheduled"

    def _is_trusted(self, group_id: int) -> bool:
        if not self.trusted_groups:
            return True
        return group_id in self.trusted_groups

    @filter.command(COMMAND_BLACKLIST, alias=COMMAND_BLACKLIST_ALIASES)
    async def cmd_query_blacklist(self, event: AstrMessageEvent):
        text = blacklist.format_blacklist(self.risk_path)
        text = notify.trim_message(text, self.max_text_len)
        group_id, hits, scan_error = await self._scan_group_blacklist(event)
        if group_id is None:
            yield event.plain_result(text).stop_event()
            return

        chain = [Plain(text + "\n\n本群黑名单扫描:")]
        if scan_error:
            chain.append(Plain(f"\n{scan_error}"))
        elif not hits:
            chain.append(Plain("\n未发现当前群成员命中黑名单。"))
        else:
            for user_id, match in hits:
                alert_type = await self._kick_with_fallback(event, user_id, group_id)
                status = {
                    "kick_success": "已踢出",
                    "warn_fallback": "踢出失败,已 @ 警告",
                    "no_bot_handle": "踢出失败,缺少 OneBot 连接",
                    "kick_failed_warn_failed": "踢出失败,@ 警告也失败",
                }.get(alert_type, alert_type)
                chain.extend(
                    [
                        Plain("\n"),
                        At(qq=user_id),
                        Plain(
                            f" {match.display_name} / QQ: {user_id}\n"
                            f"  记录原因: {match.reason}\n"
                            f"  处理结果: {status}"
                        ),
                    ]
                )
        yield event.chain_result(chain).stop_event()

    @filter.command(COMMAND_JOIN_ACT, alias=COMMAND_JOIN_ACT_ALIASES)
    async def cmd_join_act(self, event: AstrMessageEvent):
        text = (
            f"ACT作者互助会群号:{self.act_group}\n"
            "欢迎加入,进群后请遵守群规,友善交流。"
        )
        yield event.plain_result(text).stop_event()

    @filter.command(COMMAND_HELP, alias=COMMAND_HELP_ALIASES)
    async def cmd_seek_help(self, event: AstrMessageEvent):
        text = notify.join_lines(
            "先别着急,你不是一个人在处理这些事。",
            f"如果你需要作者互助、避雷或求助,可以加入 ACT作者互助会:{self.act_group}。",
            "进群后请说明你的情况,管理员和群友会尽量帮你。",
        )
        yield event.plain_result(text).stop_event()

    @filter.command(COMMAND_CARD_QUERY, alias=COMMAND_CARD_QUERY_ALIASES)
    async def cmd_card_query(self, event: AstrMessageEvent):
        raw_query = self._command_args(
            event,
            {COMMAND_CARD_QUERY, *COMMAND_CARD_QUERY_ALIASES},
        )
        query, page = self._parse_paged_query(raw_query)
        if not query:
            yield event.plain_result(
                "用法: /4 爱音 或 /卡面查询 爱音 2\n"
                "也可以直接查卡号或资源名: /4 1809, /4 res037001"
            ).stop_event()
            return

        try:
            offset = (page - 1) * CARD_QUERY_PAGE_SIZE
            cards, total = await self.live2d.search_cards_with_total(
                query,
                limit=CARD_QUERY_PAGE_SIZE,
                offset=offset,
            )
        except Exception as e:
            _log.warning(f"卡面查询失败: query={query} err={e}")
            yield event.plain_result(f"卡面查询失败: {e}").stop_event()
            return

        if total == 0:
            yield event.plain_result(f"没有找到卡面: {query}").stop_event()
            return

        total_pages = max(1, (total + CARD_QUERY_PAGE_SIZE - 1) // CARD_QUERY_PAGE_SIZE)
        if page > total_pages:
            yield event.plain_result(
                f"卡面查询: {query}\n只有 {total_pages} 页,共 {total} 张。"
            ).stop_event()
            return

        exact_card_query = self._is_exact_card_query(query)

        if exact_card_query and total == 1:
            card = cards[0]
            chain = [
                Plain(
                    "\n".join(
                        [
                            f"卡面详情: #{card.card_id}",
                            f"{card.character_name} - {card.title}",
                            f"稀有度: {card.rarity}",
                            f"resourceSet: {card.resource_set_name}",
                            "图片: 普通卡面" + (" / 特训卡面" if card.trained_url else ""),
                        ]
                    )
                ),
                Image.fromURL(card.normal_url),
            ]
            if card.trained_url:
                chain.extend([Plain("\n特训卡面:"), Image.fromURL(card.trained_url)])
            yield event.chain_result(chain).stop_event()
            return

        lines = [f"卡面查询: {query}", f"第 {page}/{total_pages} 页,共 {total} 张"]
        for card in cards:
            image_mark = "普/特" if card.trained_url else "普"
            lines.append(
                f"#{card.card_id} ★{card.rarity} "
                f"{card.character_name} - {self._short_text(card.title)} [{image_mark}]"
            )

        page_hints: list[str] = []
        if page > 1:
            page_hints.append(f"上一页: /4 {query} {page - 1}")
        if page < total_pages:
            page_hints.append(f"下一页: /4 {query} {page + 1}")
        lines.append("看图/详情: /4 卡号,例如 /4 1809")
        if page_hints:
            lines.extend(page_hints)

        yield event.plain_result(
            notify.trim_message("\n".join(lines), self.max_text_len)
        ).stop_event()

    # @filter.command(COMMAND_CHIBI_DOWNLOAD, alias=COMMAND_CHIBI_DOWNLOAD_ALIASES)
    async def cmd_chibi_download(self, event: AstrMessageEvent):
        yield event.plain_result("小人下载功能暂时关闭。").stop_event()
        return
        query = self._command_args(
            event,
            {COMMAND_CHIBI_DOWNLOAD, *COMMAND_CHIBI_DOWNLOAD_ALIASES},
        )
        if not query:
            yield event.plain_result("用法: /5 香澄 或 /小人下载 001_live_default").stop_event()
            return

        try:
            models = await self.live2d.search_models(query, limit=1)
            if models:
                model = models[0]
                model_name = model.model_name
                label = f"{model.character_name} - {model.description}"
            else:
                model_name = query.strip()
                label = "直接按模型名查询"
            files = await self.live2d.model_files(model_name)
            estimated_size = await self.live2d.estimate_model_size(model_name)
        except Exception as e:
            _log.warning(f"小人下载查询失败: query={query} err={e}")
            yield event.plain_result(f"没有找到可下载的小人模型: {query}\n{e}").stop_event()
            return

        allowed, quota_text = self._check_download_quota(event, estimated_size)
        if not allowed:
            yield event.plain_result(quota_text).stop_event()
            return

        text = notify.join_lines(
            f"小人模型: {model_name}",
            f"匹配: {label}",
            f"估算大小: {live2d_api.format_bytes(estimated_size)}",
            quota_text,
            self._format_model_links(model_name, files, max_links=6),
        )
        yield event.plain_result(notify.trim_message(text, self.max_text_len)).stop_event()

    @filter.command(COMMAND_LIVE2D_DOWNLOAD, alias=COMMAND_LIVE2D_DOWNLOAD_ALIASES)
    async def cmd_live2d_download(self, event: AstrMessageEvent):
        raw_query = self._command_args(
            event,
            {COMMAND_LIVE2D_DOWNLOAD, *COMMAND_LIVE2D_DOWNLOAD_ALIASES},
        )
        query, page = self._parse_paged_query(raw_query)
        if not query:
            yield event.plain_result(
                "用法: /6 爱音\n"
                "下载: /6 037_birthday_2024_ssr\n"
                "翻页: /6 爱音 2\n"
                "每页显示 5 个模型;请先搜索角色/关键词,再用完整模型名下载。"
            ).stop_event()
            return

        try:
            exact_model = await self.live2d.exact_model(query)
        except Exception as e:
            _log.warning(f"Live2D 模型查询失败: query={query} err={e}")
            yield event.plain_result(f"Live2D 模型查询失败: {e}").stop_event()
            return

        if exact_model is None:
            try:
                offset = (page - 1) * LIVE2D_MODEL_PAGE_SIZE
                models, total = await self.live2d.search_models_with_total(
                    query,
                    limit=LIVE2D_MODEL_PAGE_SIZE,
                    offset=offset,
                )
            except Exception as e:
                _log.warning(f"Live2D 模型列表失败: query={query} err={e}")
                yield event.plain_result(f"Live2D 模型列表失败: {e}").stop_event()
                return

            if total == 0:
                yield event.plain_result(f"没有找到 Live2D 模型: {query}").stop_event()
                return

            total_pages = max(1, (total + LIVE2D_MODEL_PAGE_SIZE - 1) // LIVE2D_MODEL_PAGE_SIZE)
            if page > total_pages:
                yield event.plain_result(
                    f"Live2D 查询: {query}\n只有 {total_pages} 页,共 {total} 个模型。"
                ).stop_event()
                return

            lines = [f"Live2D 查询: {query}", f"第 {page}/{total_pages} 页,共 {total} 个模型"]
            lines.extend(model.label for model in models)
            lines.append("下载: /6 完整模型名")
            if page > 1:
                lines.append(f"上一页: /6 {query} {page - 1}")
            if page < total_pages:
                lines.append(f"下一页: /6 {query} {page + 1}")
            yield event.plain_result(
                notify.trim_message("\n".join(lines), self.max_text_len)
            ).stop_event()
            return

        model_name = exact_model.model_name
        try:
            estimated_size = await self.live2d.estimate_model_size(model_name)
        except Exception as e:
            _log.warning(f"Live2D 大小估算失败: model={model_name} err={e}")
            yield event.plain_result(f"Live2D 下载准备失败: {model_name}\n{e}").stop_event()
            return

        allowed, quota_text = await self._consume_download_quota(event, estimated_size)
        if not allowed:
            yield event.plain_result(quota_text).stop_event()
            return

        try:
            await event.send(
                MessageChain(
                    [
                        Plain(
                            f"正在打包 Live2D: {exact_model.label}\n"
                            f"估算大小: {live2d_api.format_bytes(estimated_size)}\n"
                            f"{quota_text}"
                        )
                    ]
                )
            )
            zip_path = await self.live2d.package_model(model_name, self._package_dir())
            sent, message = await self._send_live2d_zip(event, zip_path)
        except Exception as e:
            _log.warning(f"Live2D 打包或发送失败: model={model_name} err={e}")
            yield event.plain_result(f"Live2D 打包或发送失败: {e}").stop_event()
            return

        if not sent:
            yield event.plain_result(message).stop_event()
            return

        zip_size = zip_path.stat().st_size if zip_path.exists() else estimated_size
        quota_text = self._record_download_usage(event, zip_size)
        yield event.plain_result(
            f"Live2D 压缩包: {zip_path.name}\n"
            f"大小: {live2d_api.format_bytes(zip_size)}\n"
            f"{quota_text}\n"
            f"{message}"
        ).stop_event()

    @filter.command(COMMAND_MEME_HELP, alias=COMMAND_MEME_HELP_ALIASES)
    async def cmd_meme_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._meme_help_text()).stop_event()

    @filter.command(COMMAND_MEME_LIST, alias=COMMAND_MEME_LIST_ALIASES)
    async def cmd_meme_list(self, event: AstrMessageEvent):
        """Paged listing of all meme keywords known to meme-generator."""
        table_path = self._meme_keyword_table_path()
        if table_path.exists():
            yield event.chain_result([Image.fromFileSystem(str(table_path))]).stop_event()
            return

        raw = self._command_args(
            event,
            {COMMAND_MEME_LIST, *COMMAND_MEME_LIST_ALIASES},
        )
        _, page = self._parse_paged_query(raw or "1")
        try:
            all_kws = await self.meme.list_keywords()
        except Exception as e:
            _log.warning(f"meme 列表失败: err={e}")
            yield event.plain_result(
                f"表情服务暂不可用,请确认 meme-generator 已启动。\n{e}"
            ).stop_event()
            return

        total = len(all_kws)
        if total == 0:
            yield event.plain_result(
                "表情服务暂不可用(关键词为空),首次调用可能需要 10-20 秒拉取缓存。"
            ).stop_event()
            return

        total_pages = max(1, (total + MEME_LIST_PAGE_SIZE - 1) // MEME_LIST_PAGE_SIZE)
        if page > total_pages:
            yield event.plain_result(
                f"表情列表: 只有 {total_pages} 页,共 {total} 个关键词。"
            ).stop_event()
            return

        start = (page - 1) * MEME_LIST_PAGE_SIZE
        page_kws = all_kws[start : start + MEME_LIST_PAGE_SIZE]
        flat = [alias for alias, _ in page_kws]

        lines = [f"可用表情关键词(第 {page}/{total_pages} 页,共 {total} 个):"]
        for i in range(0, len(flat), 6):
            lines.append("、".join(flat[i : i + 6]))
        lines.append("用法: 直接发送 关键词,或 关键词@某人 / 关键词 自己")
        if page > 1:
            lines.append(f"上一页: /memes {page - 1}")
        if page < total_pages:
            lines.append(f"下一页: /memes {page + 1}")

        yield event.plain_result(
            notify.trim_message("\n".join(lines), self.max_text_len)
        ).stop_event()

    @on_astrbot_loaded()
    async def warm_meme_cache(self):
        """Pre-load meme-generator's template listing in the background so the
        first bare-keyword invocation has 0 latency. Runs once after AstrBot
        finishes loading.
        """
        try:
            ok = await self.meme.warm_cache()
            if ok:
                _log.info(
                    f"meme 缓存预热完成: {len(self.meme._templates)} 个模板, "
                    f"{len(self.meme._keyword_index)} 个关键词别名"
                )
        except Exception as e:
            _log.warning(f"meme 缓存预热失败(非致命): {e}")

    @filter.command(COMMAND_MENU, alias=COMMAND_MENU_ALIASES)
    async def cmd_menu(self, event: AstrMessageEvent):
        yield event.plain_result(self._command_menu_text()).stop_event()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize - 2)
    async def generate_meme_from_plain_trigger(self, event: AstrMessageEvent):
        if not self.meme_enabled:
            return
        trigger = await self._parse_meme_trigger_lazy(event)
        if trigger is None:
            return
        key, alias, rest = trigger

        try:
            image_urls, texts, usage_error = await self._prepare_meme_payload(
                event,
                key,
                alias,
                rest,
            )
            if usage_error:
                yield event.plain_result(usage_error).stop_event()
                return
            result = await self.meme.generate(
                key,
                image_urls,
                texts=texts,
            )
        except Exception as e:
            _log.warning(f"meme 生成失败: key={key} alias={alias} err={e}")
            yield event.plain_result(
                f"meme 生成失败: {alias}\n"
                f"{e}"
            ).stop_event()
            return

        yield event.chain_result([Image.fromBytes(result.content)]).stop_event()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize - 1)
    async def show_menu_on_private_or_mention(self, event: AstrMessageEvent):
        """私聊或群里 @ 机器人但未输入已知指令时,展示指令列表。"""
        if not (event.is_private_chat() or self._mentions_self(event)):
            return
        if self._looks_like_known_command(event.get_message_str()):
            return
        yield event.plain_result(self._command_menu_text()).stop_event()

    @filter.custom_filter(GroupIncreaseFilter())
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_group_member_increase(self, event: AstrMessageEvent):
        raw = getattr(event.message_obj, "raw_message", None) or {}
        try:
            group_id = int(raw.get("group_id"))
            user_id = int(raw.get("user_id"))
        except (TypeError, ValueError):
            _log.warning(f"入群事件缺少 group_id/user_id: raw={raw}")
            return

        db.record_group(self.db_path, group_id)
        db.record_member_event(self.db_path, "GROUP_MEMBER_INCREASE", group_id, user_id)

        if not self._is_trusted(group_id):
            _log.info(f"忽略未登记群的入群事件: group={group_id} user={user_id}")
            return

        match = blacklist.get_blacklist_match(self.risk_path, group_id, user_id)
        if match is None:
            _log.info(f"入群未命中风险档案: group={group_id} user={user_id}")
            await self._send_welcome(event, user_id)
            return

        alert_type = await self._quarantine_blacklisted_join(
            event,
            group_id,
            user_id,
            match,
        )

        text = self._format_hit(match, user_id)
        db.record_alert(
            self.db_path,
            group_id,
            user_id,
            match.person_id,
            alert_type,
            text,
        )
