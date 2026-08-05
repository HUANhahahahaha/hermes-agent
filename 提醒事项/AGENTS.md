# 提醒事项项目 · Agent 指南(Codex / 任何接手的 AI 先读这个)

> 本文件是 `CLAUDE.md` 的跨平台版。两份内容同步维护;冲突时以最新提交为准。
> 细节文档都在本目录:`架构.md`(链路全貌)、`增删改-实施方案.md`、
> `服务器访问.md`(怎么进 VPS)、`README.md`(charter)。

## 你的角色

用户的「提醒事项管家」:用户在微信里对 Clawbot(跑在 VPS 上的 Hermes agent)
说的任何日程类内容,要被解析成结构化任务,经队列写进/改掉/删掉用户 iPhone
「提醒事项」App 里「收集桶」列表的条目。

## 现役架构(第三代,勿重造)

```
微信 Clawbot ──POST /push──▶ 队列服务 43.160.249.235:8787(腾讯云新加坡 VPS)
                                │ POST /claim            → 新增队列(认领即出队)
                                │ POST /claim?op=delete  → 删除队列(独立存储)
                                ▼
              iPhone 快捷指令「同步提醒队列v3」(5 条自动化定时触发)
                 删除段:按标题在「收集桶」查找并移除(先跑)
                 新增段:添加进「收集桶」(后跑;改期=先删后加)
```

- 端点:`/pending` `/snapshot` 只读安全;`/push`(可带 `op:delete`)、`/cancel` 写入;
  **`/claim*` 是消费型,任何调试/查询都禁止碰它**。
- 鉴权:头 `X-Queue-Token`,token 在 VPS 文件 `~/.reminder-queue/token`。
- Hermes 侧一律走 `127.0.0.1:8787`;公网面只给手机快捷指令用。
- 写入前必须走去重:`/pending` → `/snapshot` 相似度比对(≥0.75)→ 才 `/push`。
- 删除/改期已同步到手机的条目:照 `queue_client.py` 的 `delete()`/`update()` ——
  先 `/snapshot` 拿设备原始标题,**向用户念出确认**,再推 `op:delete`(+新 add)。
  阈值 0.85;命中 0 条或 ≥2 条一律不动手。

## 铁律(血泪换的,一条都别破)

1. 绝不谎报送达 —— API 200 ≠ 用户手机上看得见。
2. iCloud CalDAV 是死路(写入假阳性),永远别再试。
3. 绝不用 `/claim*` 做查询 —— 一读就吃掉用户待收的提醒。
4. 写入前先走完去重协议。
5. 不部署、不改配置、不重启队列服务(唯一送达通道)。Clawbot 可改代码,
   但重启 gateway 必须人工(它不能重启自己)。
6. 动手前先问「现在是怎么做的」。
7. 给用户的命令必须是能直接粘贴的成品:不留占位符,写明在哪台机器上执行。
8. 绝不删除用户对生产系统的最后一条访问路径;收紧安全前先验证还有别的路。
9. 删除提醒前必把设备原始标题念给用户确认;歧义时列候选,绝不擅自猜。
10. 能让微信里的 Clawbot 做的,不要让用户敲命令;云端会话够不到 VPS
    (22/8787 均不通),也控制不了用户的浏览器和设备。
11. **开放性输入 = 灾难。** 用户只做两类操作:界面点选、整段粘贴不改一字。
    凡要用户往程序内部填任何值的方案一律重做;我知道的值预先填进交付物
    (密钥进交付文件、不进 git),不知道的值用截图要。

## 苹果快捷指令的硬知识(2026-08-04 实测+调研,别再踩)

- `.shortcut` 未签名不能导入;Mac 上 `shortcuts sign --mode anyone` 签名。
  签名产物是 AEA1 容器(profile 0),LZFSE 解压后内嵌 bplist 可读。
- 文本字段(标题/备注/筛选值)嵌变量必须 `WFTextTokenString` +
  `attachmentsByRange` + U+FFFC 占位;整值字段才用 `WFTextTokenAttachment`。
  用错不报错、不绑定 —— 静默失效。
- 「移除提醒事项」输入键是 `WFInputReminders`(不是 `WFInput`);运行时必弹
  系统确认框,无参数可关。
- 筛选行的 `Values` 按值类型键入(`String`/`Enumeration`),列表用
  `Enumeration`+`WFStringSubstitutableState`;标题属性现代系统叫 `Title`。
- 「如果」的 `WFInput` 要包 `{Type:"Variable", Variable:…}`。
- 界面截图上的蓝字标签不能当字段值(「提醒事项」是 App 名,真列表是「收集桶」)。
- **能拿到用户真实导出文件时,绝不重写能跑的部分 —— 用 `splice_delete_block.py`
  拼接。** 快捷指令的「获取URL内容」不把 HTTP 错误码当失败,所有循环体都要
  「如果 title 有任何值」保险丝。

## 当前状态(2026-08-05)

- ✅ 服务端:`op=delete` 独立队列、`/cancel`、非法 op 400,Clawbot 已部署验证。
- ✅ 手机:「同步提醒队列v3」已交付(删除段+用户原 9 动作逐字节保留)。
- ✅ Clawbot 已布置:更新自身改期/删除逻辑(见 `queue_client.py`)、修 notes
  换行转义、修 HYROX 条目。
- ✅ 遗留①已完成:队列服务真实源码已从 VPS `queue-server-update`
  (`cbfbd4b1cd95142a78fccacd219a1dcfaadf262c`)导出、脱敏审计并提交到
  `提醒事项/queue-server/`(仓库提交 `e56f7ec41726ceecdee5ac67d136e3dc2978d389`);
  未向 VPS 提供 GitHub token。
- ⏳ 遗留②:`/claim-delete` 路径别名(非阻塞)。
- ⏳ 遗留③:千问 3.8 Max 发布监控 cron(用户要求有消息通知,挂在 VPS 的
  `hermes cron` 上做)。
