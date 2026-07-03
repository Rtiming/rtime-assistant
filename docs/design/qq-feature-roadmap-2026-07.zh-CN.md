# QQ 机器人功能扩展开发计划(2026-07-04)

状态:**规划,未开发**(owner 2026-07-04:先完善模块化,这些扩展交给后续开发者)。
本文只列清楚"做什么、在哪做、注意什么",供他人接手。每项都应做成 QQ 模块下可选、
能开关(与 modules.json channel-qq 的模块化一致,见 docs/reference/modules.zh-CN.md)。

代码位置(现状):apps/qq-bridge/qq_bridge/。入站解析 onebot/cqcode.py,媒体 media.py,
事件分发 onebot/ws_server.py(post_type message/notice/request/meta_event),
出站 output_qq.py,消息处理 app.py。

## 一、QQ 自带表情解析完善(已有基础,补全即可)
现状:cqcode.py 的 `QQ_FACE_NAMES` 表已把常用 face_id 映射成中文含义(惊讶/撇嘴/…),
入站 QQ 内置表情**已转成文字、不下载不验证**——正是"提前解析"的正确做法。MediaSegment
已识别 face(经典表情)/mface(商城表情/斗图)/sticker。
待做:
- 补全 `QQ_FACE_NAMES`(现"仅常用");完整表可从 NapCat/OneBot face 表导入。
- mface(商城表情)取其 `summary`/文字描述塞进给模型的文本(现在可能只当图)。
- **owner 注记(重要)**:表情的"文字名"和它的"实际语义"可能有出入(如"[微笑]"在年轻人语境
  常含贬义)。**先只做字面文字解析**,语义映射(表情→真实情绪)是独立的后续项,别混做。
影响面:cqcode.QQ_FACE_NAMES + extract_plain_text 的表情文本注入。纯入站,低风险。

## 二、拍一拍(poke)支持
现状:ws_server.py 已收 notice 事件(post_type=notice),但未对 poke 做响应。
拍一拍是 notice:`notice_type=notify, sub_type=poke`(群/私聊都有),带 target_id/user_id。
待做:
- events.py/app.py 加 poke notice 分支:被拍(target_id==self)时触发回应。
- 回应策略做成可配:①拍回去(send_poke / group_poke 动作)②发一句话③交给模型生成回应。
- 频率限制(防对拍刷屏)。
影响面:新增 notice 处理分支 + 一个出站 poke 动作。中等。

## 三、表情包/表情回复(出站)
现状:bot 只回文本(output_qq.py split_for_qq)。要主动回 QQ 表情/表情包需出站 face/mface 段。
待做:
- 出站指令(仿现有 `[[rtime-send-image:…]]` 的 directive 机制,见 media 出站):加
  `[[rtime-send-face:<id>]]`(经典表情)/ `[[rtime-send-sticker:…]]`(商城表情或图片表情包)。
- 模型系统提示里告知可用表情集(小白名单),让模型在合适时机点用。
- 表情包库(可选):本地放一组常用表情包图,模型按名点用,base64 出站(复用 media 出站路径)。
影响面:output_qq/media 出站 + 提示词。中等。

## 四、模块化要求(所有以上项共同)
- 每项都在 channel-qq 模块下,做成**可开关的子能力**(env/profile 开关,默认关或保守),
  不想要的部署不受影响——与"渠道热插拔"一致(modules.json)。
- 入站解析类(一)对所有部署无害可默认开;交互类(二/三)默认关,按 profile/env 开。
- 配套 schema 字段进 admin-core registry(qq 模块),面板可配(与现有 QQ 配置一致)。

## 五、非目标 / 明确暂缓
- 表情"语义"映射(表情→真实情绪)——owner 明确先不做,只做字面解析。
- 语音(record)STT——media.py 现注明"故意不做"。
- 这些是"友好度/趣味"增强,不影响核心答疑;优先级低于稳定性与模块化。
