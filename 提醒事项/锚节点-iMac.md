# iMac 锚节点 —— 部署手册

> 目标：把你**常开的 iMac**（办公桌那台）变成"提醒事项共享总线"的主力节点。
> 它用 `remindctl` 直接读写本机那份**和手机同步好的真库**，绕开走不通的 CalDAV。
>
> 前提：iMac 登录的 Apple ID = `ifoon@me.com`（和手机同一个），且
> 系统设置 → Apple ID → iCloud → 提醒事项 = **开**。

---

## 一次性安装

```bash
brew install steipete/tap/remindctl
remindctl authorize            # 弹窗点"好"，给 Reminders 权限
git pull origin claude/stoic-darwin-agw0p9
cd 提醒事项
```

## 第 0 步（最重要）：真验证

上一会话的"已验证"是 CalDAV 自己骗自己的假阳性。这次在设备上亲眼确认：

```bash
python3 anchor_node.py verify
```

看输出里的列表**有没有**你手机上的「收集桶 / 项目 / 报销提醒」。

- **有** → 成了。iMac 看到的就是真库，往下走。
- **没有** → 这台 iMac 没登 `ifoon@me.com` 或没开 iCloud 提醒同步，先修这个，别往下。

## 第 1 步：建总线 + 冒烟测试

```bash
python3 anchor_node.py bus-init                          # 建 Hermes-Bus 列表
python3 anchor_node.py post "锚节点测试 ✅" --from imac    # 写一条
```

**去手机看**：几秒~1 分钟后，「Hermes-Bus」列表里应出现"锚节点测试 ✅"。
**这一步看到了，才算真打通**（和今晚 CalDAV 的区别就在这）。

## 第 2 步：补上今晚没真正送达的那条

```bash
remindctl add --title "跟孙建亚老师确认家前采时间（绿洲比华利花园931号楼）" \
  --list 收集桶 --due "2026-07-02 10:00" --alarm "2026-07-02 09:00"
```

> 列表名按 verify 看到的真实中文名填（可能是「收集桶」）。

## 第 3 步：常驻轮询

```bash
python3 anchor_node.py loop --interval 90
```

它每 90 秒扫一次总线，有新消息就打印。要长期常开，用 launchd（见下）。

### 开机自启（launchd）

`~/Library/LaunchAgents/com.hermes.anchor.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hermes.anchor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/完整路径/hermes-agent/提醒事项/anchor_node.py</string>
    <string>loop</string><string>--interval</string><string>90</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.hermes.anchor.plist
```

---

## 总线消息约定

- 专用列表 **`Hermes-Bus`**（别和收集桶混）。
- 标题尾带 `#hermes-bus[来源/类型]` 标记，正文+元数据 JSON 放备注（notes）。
- 处理完用 `remindctl complete <id>` 勾掉 = "已读"，避免重复处理。
- 各设备节点只认领该处理的条目，靠"认领+勾掉"兜住 iCloud 无锁、有延迟的问题。

## 拓扑

```
iMac(常开,remindctl,主力) ──┐
iPhone/iPad(快捷指令,轻节点)─┼─→  iCloud 同步  ←→  Hermes-Bus 列表（共享总线）
云端会话(重推理,不当节点)  ──┘
```

云端不直接碰你设备的提醒（今晚已证明碰不到）；它只在需要时产出内容，由 iMac 锚节点落库。
