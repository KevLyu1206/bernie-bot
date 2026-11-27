# bernie bot

一个跑在自己电脑上的 QQ 群机器人，人格是《火焰纹章：风花雪月》的贝尔娜提塔。

不是客服，不是问答机——她会**延迟回话、分条发、被催了会不耐烦、好感度分五个阶段**，
外加一整套 maimai DX 工具链：查分、曲库、猜曲、抽卡、运势。

```
QQ  ←→  LuckyLillia（协议端，OneBot v11）  ←→  bot.py  ←→  DeepSeek / 任意 OpenAI 兼容接口
                                                       ↘  水鱼 / 落雪 / 本地曲绘缓存
```

**零服务器**：不需要公网 IP、不需要备案，协议端主动连本机的 WebSocket。

---

## 功能

### 人格

- **五阶段好感度**（门缝 → 试探 → 依赖 → 心动 → 恋人），按 QQ 号独立累积
- **"主人"这个称呼唯一**，只绑 `master_ids`；别人最多到恋人也不叫主人
- 角色卡是 Character Card V2，路径写在 `config.card`，换卡即换人格
- 世界书按关键词注入设定，不塞满整个 context
- **非主人且阶段 <3 时注入硬性约束**：禁止任何暧昧、身体接触、性相关描写

### 说话方式

- **打字延迟**：主人 10 秒，群友 30 秒（不是冷却，是"她打字慢"）
- **分条发送**：群里 2 条、私聊最多 5 条，每条 ≤45 字，间隔 0.6–1.6 秒
- **打断**：延迟期间再催一句，回一句催促话术，一轮只催一次
- **长度自适应**：日常三句以内；只有真的进入亲密场景才放开篇幅

### 群聊策略

- **不 @ 不理**（`at_only`），关键词和随机触发全关
- **群里默认不跑 LLM**，只响应指令——220 人的群，聊天会烧穿预算
- 私聊才能聊天，全接

### maimai DX

| 功能 | 说明 |
|---|---|
| **B50 查分** | 水鱼、落雪两个源独立指令；按 QQ 或用户名查 |
| **B50 渲染** | 仿官方 5 列网格，Apple 风白卡；牌子、段位、评级逐字配色 |
| **理论最高 B50** | `/b50 max`，按版本取定数最高的曲，101% 达成率 |
| **曲库** | 1360 首，查定数 / 最近更新 / 随机抽歌 |
| **猜曲** | 阶梯提示（曲风 → BPM → 曲师 → 难度 → 曲绘），15 秒一条，别名算对 |
| **定数表** | 按定数出曲绘墙长图 |
| **抽卡** | 1628 张曲绘，按难度分五档稀有度，每天两张 |
| **卡包 / 卡池** | 按稀有度看收藏，可 @ 别人看他的收集 |
| **欧非榜** | 按群统计运气排行，带头像 |

### 其他

- **今日运势**：分数 / 洋快餐 / 曲目 / Phigros / 冰淇淋，每人每天一份，可重抽一次
- **5C 食堂**：克莱蒙特五个食堂今天早中晚吃什么（主人专属）
- **西八 / 烧烤**：随机贴图 + 计分 + 点赞榜
- **记忆压缩**：攒够条数自动总结成摘要，删掉原文，只留摘要
- **费用统计**：每人每日 2 元、全局 8 元，`/cost` 看用量

---

## 快速开始

### 1. 协议端

装 [LuckyLillia](https://github.com/LLOneBot/LuckyLillia)（原 LLOneBot），登录 QQ 小号。

配置里只留 **反向 WebSocket**，指向：

```
ws://127.0.0.1:8080/onebot/v11/ws
```

正向 WS 必须关掉，否则会抢端口。

### 2. Bot

```bash
pip install -r requirements.txt
cp config.example.json config.json
# 填 llm.api_key、master_ids、onebot.port
python bot.py
```

看到 `监听 ws://0.0.0.0:8080` 就成了。

### 3. 启动顺序

```
先 bot.py  →  再 LuckyLillia
```

反了会一直 `ECONNREFUSED`（协议端每 60 秒重试一次，会自己连上，但要等）。

---

## 指令

群里要 @，标 ★ 的不用 @、斜杠可有可无。私聊直接发。

| 指令 | 做什么 |
|---|---|
| `/b50` | 查自己的 B50（先在水鱼绑 QQ） |
| `/b50 用户名` | 查别人 |
| `/b50 max` | 理论最高 B50 |
| `/lxns` | 落雪查分（跟水鱼分开） |
| `/song 关键词` | 查曲目 ID 和定数 |
| `/new` | 最近更新了什么曲 |
| `/quiz` | 阶梯提示猜曲 |
| `/random 13+` | 随机抽一首 |
| `/随一首 14` `/定数表 14` | 定数抽歌 / 曲绘墙 |
| `/cost` | 今日用量 |
| `今日运势` ★ | 今日签 |
| `重新抽签` ★ | 重摇一份（每天一次） |
| `抽卡` ★ | 抽两张曲绘 |
| `卡包` ★ `卡池 UR` ★ `欧非榜` ★ | 看收藏和排行 |
| `随机西八` ★ `随机烧烤` ★ | 随机贴图 |
| `/5C吃什么` | 今天食堂吃什么（主人专属） |
| `/help` | 全部指令 |

---

## 配置要点

```json
{
  "llm": {
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "sk-..."
  },
  "card": "我的角色卡.json",
  "master_ids": [123456789],
  "group": {
    "at_only": true,
    "chat_enabled": false,
    "user_cooldown_seconds": 60
  },
  "reply_delay": { "master": 10, "user": 30 },
  "budget": {
    "user_daily_cny": 2.0,
    "global_daily_cny": 8.0
  },
  "maimai": {
    "enable": true,
    "lxns_key": ""
  }
}
```

完整字段见 `config.example.json`。

---

## 目录

```
bot.py                  主程序：WS 服务、限流、节奏、成本、指令分发
console.py              本地测试台，不接 QQ 也能聊
maimai.py               查分 + B50 渲染（Pillow）
songdb.py               落雪曲库（1360 首）
gacha.py                抽卡 / 卡包 / 卡池 / 欧非榜
fortune.py              今日运势
songcard.py             定数抽歌 / 曲绘墙
menu5c.py               5C 食堂
xiba.py                 西八计分
memory.py               记忆导出成 Markdown
plugins/bernie/
  persona.py            提示词分层组装
  worldbook.py          世界书关键词触发
```

---

## 数据

曲绘、牌子、曲库都是现拉现缓存，不进仓库：

```bash
python prefetch_covers.py     # 1360 张曲绘（水鱼 → 落雪 → 占位）
python prefetch_plates.py     # 400 张官方牌子
```

数据源：

- 曲绘 `https://www.diving-fish.com/covers/{id:05d}.png`
- 落雪 `https://assets.lxns.net/maimai/jacket/{id}.png`
- 牌子 `https://assets.lxns.net/maimai/plate/{id}.png`
- 曲库 `https://maimai.lxns.net/api/v0/maimai/song/list`

---

## 测试

```bash
python selftest.py        # 离线自检
python group_test.py      # 群聊规则
python budget_test.py     # 预算闸门
python gacha_test.py      # 抽卡
python fortune_test.py    # 运势
python songcard_test.py   # 定数
python x8_cmd_test.py     # 免 @ 指令
```

二十多个测试脚本，全部离线跑（mock 掉 QQ 和外部 API）。

---

## 说明

- 角色卡、`config.json`、`state.json`、各类缓存都在 `.gitignore` 里，仓库只有代码
- 人格是数据不是代码：换一张卡、改一句提示词，就是另一个人
- 群里默认不聊天是刻意的：成本可控，也避免刷屏

## License

MIT
