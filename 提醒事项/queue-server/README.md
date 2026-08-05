# 提醒队列服务(queue_server.py)

跑在 VPS 上的极简 HTTP 服务(纯标准库,零依赖)。负责在 hermes agent
解析出的提醒和最终消费端（iPhone 快捷指令 / Mac 消费脚本）之间做一层
排队缓冲。**这是唯一真正写入用户「提醒事项」App 的通道**，此前只存在于
生产 VPS 上、没有任何备份，本次一并把源码提交进仓库以消除单点风险。

## 部署

```bash
mkdir -p ~/.reminder-queue
cp queue_server.py ~/.reminder-queue/queue_server.py
echo "<替换成一个随机生成的强 token，不要用示例值>" > ~/.reminder-queue/token
chmod 600 ~/.reminder-queue/token
python3 ~/.reminder-queue/queue_server.py   # 或用 systemd 常驻，见下方 unit 示例
```

生产环境用 systemd 常驻（`/etc/systemd/system/reminder-queue.service`）：

```ini
[Unit]
Description=Reminder write queue for Apple Reminders consumers
After=network.target

[Service]
User=<your-user>
ExecStart=/usr/bin/python3 /home/<your-user>/.reminder-queue/queue_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

监听端口固定 `8787`。生产上 Hermes 侧一律走 `http://127.0.0.1:8787`
（不出公网）；公网 `<公网IP>:8787` 那一面是给 iPhone 快捷指令 `GET/POST /claim`
用的，需要在防火墙/安全组单独放行。

## 存储

- `~/.reminder-queue/queue.json` —— 普通新增(op=add)队列，append-only + 状态标记。
- `~/.reminder-queue/delete_queue.json` —— 待删(op=delete)队列，**与 add 队列
  完全分开存放**，避免老版本快捷指令 `GET /claim` 不带参数时把删除指令误当
  新增条目写进手机。
- `~/.reminder-queue/snapshot.json` —— Mac 端上传的"设备上已有哪些提醒"快照，只读参考。
- `~/.reminder-queue/token` —— 鉴权 token，**不提交进仓库，本目录不含真实密钥**。

## 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | `{"ok": true, "pending": N}` |
| `/push` | POST | 入队。body: `{title, due?, remind?, list?, notes?, source?, replace?, op?}`。`op` 默认 `"add"`，可选 `"delete"`（删除条目只需要 `title`，进独立队列）。缺省行为与历史版本完全一致。 |
| `/pending` | GET | 查 add 队列里未认领的条目，只读不消费。 |
| `/claim` | GET/POST | 认领并出队。不带参数 = 只认领 add 队列（**历史行为，务必保持**，防止老快捷指令把删除指令当新增写入）。`?op=delete` 单独认领 delete 队列。**消费型端点，调试/查询绝对不要调用它**，一读就把用户待收的提醒/待删指令吃掉。 |
| `/claim-delete` | GET | `/claim?op=delete` 的路径别名，供不便拼接查询参数的消费端使用；只认领 delete 队列。它同样是**消费型端点**。 |
| `/cancel` | POST | body: `{title}`。撤掉 add 队列里**尚未被认领**的同名条目，返回 `{"ok": true, "cancelled": N}`。只作用于队列，碰不到已经落进手机的提醒。 |
| `/ack` | POST | body: `{id, result}`，逐条回执用（Mac 消费脚本用）。 |
| `/snapshot` | GET/POST | Mac 端上传/查询设备上已有提醒的快照。 |

鉴权：所有请求带 header `X-Queue-Token: <token>`。

## 安全边界

- `/claim`、`/claim-delete` 出队即消费，**任何调试/排障场景都不要调用它们**，否则会把用户
  真正待收的提醒/待删指令吃掉。查询只用 `/pending` 和 `/snapshot`。
- delete 队列与 add 队列物理隔离，任何改动都不能让两者混放，否则老版本
  快捷指令会把删除指令误写成一条新提醒。
- 删除不可撤销，且下游（快捷指令）只能按标题精确匹配，没有 ID 可回传，
  所以调用方在真正下删除指令前必须让用户确认过 `exact_title`。
