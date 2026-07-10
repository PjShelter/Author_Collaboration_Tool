# 二创作者联合工具
最近几天webgal和新春会的部分成员和本身都在遭到一个xtt进行攻击

似乎叫crychic复辟吧，虽然我们处理了这件事

但是在后续过程中我们得知了有许多作者因为遭到类似的行为

他们的视频和文被批量举报下架，他们的名字被用在皮套在pixiv上发bp文章

较有名气的作者受到攻击尚且能够防御，小作者收到这种对待只能忍气吞声

[![qq群](qq%E7%BE%A4.jpg)](qq%E7%BE%A4.jpg)

所以我代表webgal的管理成员宣布

任何受到这种类似攻击行为

比如被造谣，被攻击，二创被举报下架

请加入并联系我们，如果你愿意为了保护我们的作者做出努力也请来帮忙

有影响力的up应该联合起来保护小up，不能让更多的二创作者受到无端的诋毁和攻击了

## 风险人员黑名单

公开共享的风险人员档案（数据源：[`AstrBot/data/plugins/author_collaboration/data/risk_profiles.yaml`](AstrBot/data/plugins/author_collaboration/data/risk_profiles.yaml)）。bot 部署后会在入群事件中自动比对，命中者将被禁言 60 秒、延迟踢出并拒绝再次加群。

| 名字 | QQ | 等级 | 录入时间 | 原因 |
|---|---|---|---|---|
| 井田诗织 | `1744916568` | high | 2026-07-09 | 造谣并暗示画师涉嫌 bp；造谣 webgal 相关二创作者和新春会包庇 bp |
| knighV | `1559641017` | high | 2026-07-09 | 造谣新春会；撕咬无关爱素作者 lyy；造谣 lyy 画师 bp；造谣他人开盒、bp 等 |

**匹配规则**：上表两条记录使用 `group_id: -1` 通配，表示对应 QQ 号在所有受信任群触发。bot 默认行为是命中即处理，不私聊警告、不公开群内通报详情。

**提交新条目**：修改 `data/risk_profiles.yaml`，按现有 schema 追加 `profiles` 条目后提 PR。`mapped_members` 的 `group_id: -1` 表示该 QQ 在所有群生效；指定特定群时填真实群号。

## 相关仓库

本仓库基于以下项目：

| 项目 | 用途 | 链接 |
|---|---|---|
| AstrBot | 机器人框架（v4.23.5，已 vendor 进 `AstrBot/`） | <https://github.com/Soulter/AstrBot> |
| napcat-docker | QQ OneBot v11 适配（通过反向 WS 与 AstrBot 通信） | <https://github.com/mlikiowa/napcat-docker> |
| meme-generator | FastAPI 后端，提供 687 个 meme 模板的渲染服务 | <https://github.com/MeetWq/meme-generator> |
| meme_emoji | 上游 meme 模板库（本项目安装到 `meme-data/memes/meme_emoji/`） | <https://github.com/anyliew/meme_emoji> |
| Shelter Live2D 镜像 | Bestdori Live2D 模型与卡面查询的数据源 | <https://live2d.shelter.net.cn> |

