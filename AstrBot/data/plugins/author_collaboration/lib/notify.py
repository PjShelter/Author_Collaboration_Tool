"""文本裁剪 + 消息拼接小工具。

AstrBot 端发消息统一通过 event.send / event.plain_result;
本模块只负责准备字符串,不直接发消息。
"""
from __future__ import annotations


def trim_message(content: str, max_len: int = 1800) -> str:
    """把超长文本裁短,避免 QQ 单条消息 1800 字符上限。"""
    if max_len <= 0 or len(content) <= max_len:
        return content
    suffix = "\n...(内容已截断)"
    return content[: max_len - len(suffix)] + suffix


def join_lines(*lines: str) -> str:
    return "\n".join(s for s in lines if s)