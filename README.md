# 贝尔娜提塔 QQ bot · 接入指南

```
QQ小号  ──登录──>  NapCat （QQ 协议端，OneBot v11）
                      │  反向 WebSocket  ws://127.0.0.1:8080/onebot/v11/ws
                      ▼
                    bot.py  （人格 + 好感度 + 记忆，跑在你电脑上）
                      │  HTTPS
                      ▼
                  DeepSeek / Ollama / 任意 OpenAI 兼容接口
```

不需要公网 IP、不需要备案、不需要服务器。全在本地跑，NapCat 主动连你的 bot。

---

## 1. 你要准备的东西

| 项 | 说明 |
| --- | --- |
| QQ 小号 | 一个能扫码登录的号，最好已经「养」过几天（新注册号立刻挂协议端很容易被风控冻结） |
| Python | 3.10+（这台机器是 3.14.6，已装好依赖） |
| LLM API Key | DeepSeek（`https://platform.deepseek.com`）或任意 OpenAI 兼容服务；走本地 Ollama 也行 |
| NapCat | 开源 QQ 协议端，GitHub `NapNeko/NapCatQQ` 的 Releases |
| 一台不关机的电脑 | 断电就掉线，bot 和 NapCat 都要一直开着 |

---

## 2. 文件清单

| 文件 | 作用 |
| --- | --- |
| `bot.py` | 主程序：WS 服务端 + 收消息 + 调 LLM + 好感度落盘 |
| `plugins/bernie/persona.py` | 角色卡读取 + system prompt 组装（阶段、称呼、场合、输出约定） |
| `plugins/bernie/worldbook.py` | 世界书关键词触发（常驻条目 + 命中条目，单字 key 自动忽略） |
| `config.example.json` | 配置模板，复制成 `config.json` 再改 |
| `贝尔娜提塔·冯·瓦立.json` | 角色卡（15 条世界书），两个模块都直接读它 |
| `贝尔娜提塔·资料卡.md` | 设定集，人看的 |
| `selftest.py` | 离线自检：人格加载 / 世界书 / 解析 / 落盘，不连 QQ 不烧 token |
| `group_test.py` | 群聊规则测试：at_only / 按人冷却 / 每分钟上限 / 好友申请 / 尺度约束 |
| `budget_test.py` | 预算闸门测试：花费到上限 → 只回「明天再来吧」 |
| `console.py` | 终端版聊天，不接 QQ 也能先玩（同一套管道） |
| `setupll.py` | 把 LuckyLillia 的 OneBot 反向 WS 接到 8080 |
| `启动全部.bat` | 一键：检查配置 → 起 bot → 起 LuckyLillia |
| `LuckyLillia-Desktop-win-x64/` | QQ 协议端（Desktop 3.1.2 / 内核 8.3.2），已预配好反向 WS |
| `e2e_test.py` | 端到端冒烟：假 LLM + 假 NapCat，跑通「收消息→回复→存好感」全链路 |
| `start.bat` | 双击启动 |
| `state.json` | 运行时生成：每人的好感阶段和历史 |
| `bot.log` | 运行时生成：日志 |

> 改人格：只动 `贝尔娜提塔·冯·瓦立.json`（`system_prompt`、`character_book`）。
> 改行为：动 `plugins/bernie/` 里的两个模块。`bot.py` 基本不用碰。

---

## 3. 装 NapCat（QQ 协议端）

1. 打开 <https://github.com/NapNeko/NapCatQQ/releases>，下载 Windows 一键包
   （`NapCat.Shell.Windows.OneKey.zip`，自带匹配版本的 QQ）。
2. 解压到 **纯英文路径**，比如 `D:\napcat`。中文路径和空格都可能出问题。
3. 双击 `launcher.bat`（部分版本叫 `napcat.bat` / `NapCatWinBootMain.exe`）。
   首次启动会自动拉起 QQ 并弹出登录界面——**用 QQ 小号扫码登录**。
   - 也可以扫控制台打印的二维码图片（`qrcode.png`）。
   - 登录成功后控制台会打印 `WebUi Local Address: http://127.0.0.1:6099/webui`。
4. 浏览器打开 WebUI。token 在 `napcat/config/webui.json` 的 `token` 字段里，复制粘贴进去。
5. 左侧进 **网络配置** → **新建** → 类型选 **WebSocket 客户端**（这就是「反向 WS」）：
   - URL：`ws://127.0.0.1:8080/onebot/v11/ws`
   - Token：留空
   - 勾选 **启用**
   - 保存
6. 回到列表确认状态是「已连接」（此时你要先把 bot.py 跑起来，见第 5 节）。

> **QQ 版本别乱更新。** NapCat 依赖特定 QQ NT 版本，QQ 一自动升级协议端就废。
> 关掉 QQ 的自动更新，或者在 NapCat 的 releases 说明里对一下支持的版本号。

---

## 4. 填配置

复制 `config.example.json` → `config.json`，然后改这几项：

```json
{
  "llm": {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-你的key",
    "model": "deepseek-chat"
  },
  "master_ids": [你的QQ号]
}
```

- `master_ids`：**你自己（主人）的 QQ 号**。填了之后这个号会被当成「老师·唯一的主人」，其余所有人自动按「同学」处理。留空则所有人都按同学。
- 不想把 key 写进文件：改成环境变量 `LLM_API_KEY`，代码优先读环境变量。
- 用 Ollama：`"base_url": "http://127.0.0.1:11434/v1"`，`"api_key": "ollama"`，`"model": "qwen2.5:14b"` 之类。

**群聊触发参数**

| 字段 | 说明 |
| --- | --- |
| `trigger_keywords` | 消息里出现这些词就回应（「贝尔」「贝尔娜提塔」等） |
| `random_reply_prob` | 没被叫到时随机插话的概率，0.06 ≈ 十几条里冒一次 |
| `at_only` | 设 `true` 就只认 @，完全不看关键词和随机 |
| `cooldown_seconds` | 同一个群两次回复的最小间隔，防刷屏 |
| `max_chars_group` | 群里回复字数上限（默认 140，她的性格本来就短） |

---

## 5. 启动

```powershell
cd "C:\Users\Kevin\Desktop\bernie bot"
python -m pip install -r requirements.txt   # 已装可跳过
python bot.py
```

或者直接双击 `start.bat`。

看到这一行就说明在等 QQ：

```
监听 ws://0.0.0.0:8080/onebot/v11/ws —— 等 NapCat 连上来
```

NapCat 那边点「启用」后，控制台会打印：

```
QQ 端已连接：('127.0.0.1', xxxxx)
```

**然后测试**：
1. 拿大号私聊小号 → 应该收到回复。
2. 建一个只有两个号的小群 → 群里 `@小号 你好` → 应该有反应。
3. 确认没问题再拉进真的群。

**不想连 QQ 先验一遍**：

```powershell
python selftest.py    # 人格/世界书/解析/落盘，秒出结果
python e2e_test.py    # 假 LLM + 假 NapCat，整条链路跑一遍
```

两个都打印「全部通过 / OK」再往下走。

---

## 5.5 主人专用命令（私聊小号发，别人发了没用）

| 命令 | 作用 |
| --- | --- |
| `/stage` | 看自己（或 `/stage 123456` 看某人）当前阶段、消息数 |
| `/set 4` | 直接改阶段 1-5，测试用 |
| `/note 怕打雷` | 往她的「记得的事」里塞一条，之后每次对话都会带上 |
| `/reset` | 清掉某人全部记忆 |
| `/reload` | 改完角色卡热重载，不用重启 bot |

只有 `master_ids` 里的号能用，其他人发 `/` 开头的会被当成普通聊天。

---

## 5.6 每日预算闸门

花费累计到上限后，她只回一句「明天再来吧」，不再调用模型——防止半夜被人刷爆账单。

```json
"budget": {
  "enabled": true,
  "daily_cny": 3.0,                  // 每天上限（人民币）
  "prompt_price_per_m": 2.0,         // 输入 缓存未命中 ¥/百万 token
  "cache_hit_price_per_m": 0.5,      // 输入 缓存命中 ¥/百万 token
  "completion_price_per_m": 8.0,     // 输出 ¥/百万 token
  "exhausted_reply": "明天再来吧",
  "exhausted_group_per_minute": 1    // 群里每分钟最多回绝几次，防止刷屏
}
```

行为：

| 状态 | 表现 |
| --- | --- |
| 花费 < 上限 | 正常聊天 |
| 花费 ≥ 上限 | 群里被 @ / 私聊 → 只回「明天再来吧」，**不调用模型**、不计费 |
| 同一群一分钟内 | 只回绝一次，不重复说 |
| 跨天 | 日期变了自动清零，重新计费 |

**查看今天花了多少**：

```powershell
python -c "import json,time;m=json.load(open('state.json',encoding='utf-8'))['meta'];print(f\"{m['date']} 花费 {m['cost']:.3f} 元 / 输入 {m['ptok']} tok / 输出 {m['ctok']} tok / {m['calls']} 次调用\")"
```

**注意超支的一点点**：检查发生在调用**之前**，所以最后一次仍会正常回复，等于最多超出一顿单次成本。DeepSeek 一次约 3000-4000 token，单次 ≈ 0.01 元，所以实际是在 3.01 元左右停下，不用管。

**改上限**：把 `daily_cny` 改成你要的数字，跑 `python bot.py` 重启即可。改价格的话对一下 <https://platform.deepseek.com> 当前价目，填错会导致预算算不准。

---

## 6. 好感度怎么动的

不写在聊天里显眼的地方。模型只在「这一轮真发生了值得推进/后退的具体事件」时，在回复末尾偷偷输出 `[[STAGE:+1]]`；bot 把它剥掉、改 `state.json`、不发给 QQ。

想看某人到哪一阶了：

```powershell
python -c "import json;d=json.load(open('state.json',encoding='utf-8'));print({k:v['stage'] for k,v in d['users'].items()})"
```

想手动调（比如测试）：

```powershell
python -c "import json;p='state.json';d=json.load(open(p,encoding='utf-8'));d['users']['123456']['stage']=4;json.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)"
```

清空某个人的记忆：删掉 `state.json` 里对应的 block。

---

## 7. 出问题先看这里

| 现象 | 原因 / 处理 |
| --- | --- |
| bot 报「还没有 QQ 连上来」 | NapCat 的网络配置没启用，或 URL 端口写错 |
| NapCat 显示已连接但 bot 没反应 | 事件上报类型要全选（消息、通知、请求都要开），或者 QQ 号被静音/禁言了 |
| 回复很慢 | LLM 首字延迟。`max_tokens` 调小、换个快的模型，或者加流式（见下） |
| 回复太长被截断 | 调 `max_chars_group` / `max_chars_private`；截断是按句末标点切的，不会切半个字 |
| 老是说错话 / 出戏 | 检查 `state.json` 里 history 是否被污染，清掉重来；`temperature` 降到 0.9 左右 |
| 小号被冻结 | 新号风控。至少养 3-7 天（正常聊天、别加太多群），协议端登录后别频繁改资料 |
| 二维码扫不上 | QQ 版本不匹配，用 NapCat releases 里对应版本的 QQ，别自动更新 |
| 电脑睡眠后掉线 | 电源设置里关掉睡眠；掉线后要重新扫码登录 |

---

## 8. 想加的东西（都在 `bot.py` 里，改动很小）

- **流式回复**：现在是一次性等完整回复。要边生成边发得用 `stream: true` + 分段发消息，调试麻烦，不建议先用。
- **语音**：NapCat 支持发 `record` 段，接入 TTS（如 edge-tts）后把文本转音频发出去，她的音色很适合萝莉音，但会显著增加延迟。
- **图片**：刺绣/猪笼草的图可以用 `image` 段，本地放几张素材，按关键词随机发。
- **群记忆**：`STORE.groups` 已经存了每个群最近 10 条，现在是喂进 system，想让她「记得群里昨天聊过什么」可以改成落盘持久化。
- **管理命令**：`/stage`、`/reset` 这类可以加在 `handle_private` 里，只对 `master_ids` 生效。

---

## 9. 不想写代码的替代方案

如果只想快点跑起来、不在意可控性：

| 方案 | 特点 |
| --- | --- |
| **AstrBot** | 自带 WebUI、LLM 接入、人格设定、知识库，装好 NapCat 后填个地址就能用。人格直接贴 `system_prompt`，世界书要手动塞进「知识库」。最省事。 |
| **Koishi + onebot 适配器** | Node 生态，插件多，但写人格逻辑要自己来。 |
| **NoneBot2** | Python，和本项目的 `bot.py` 思路一样，只是换成框架写法。适合后期要加很多功能。 |

自己写 `bot.py` 的好处是：好感度、世界书触发、群里群外的两套行为，全都改一行代码就能调——这些恰恰是现成框架里最难塞的部分。

---

## 10. NapCat 没了怎么办（协议端替换）

QQ 协议端的宿命就是这样：被腾讯盯上→下架→改名→换个壳再来。**这一步不影响你的 bot。**

`bot.py` 只会说 OneBot v11 的一句话：

```
拔到 ws://127.0.0.1:8080/onebot/v11/ws，然后给我发 message 事件、我调 send_group_msg
```

NapCat 只是「把这句话说出来」的其中一个程序。换成任何一个 OneBot v11 实现，`bot.py` 一个字都不用改。

### 10.1 先确认现状（2026-11-21 实查）

| 项目 | 状态 |
| --- | --- |
| `NapNeko/NapCatQQ` | 仓库还在，**Releases 已清空**（API 返回空数组），下载不了 |
| `LLOneBot/LLOneBot` | 已迁到 `LuckyLilliaDev/LuckyLilliaBot`，**活跃**（v8.3.2，2026-10；topic 含 `llonebot`/`llob`） |
| `LagrangeDev/Lagrange.OneBot` | Releases 已 404 |
| `icqqjs/icqq-onebot` | **活跃**（Rust 实现，v0.7.0-rc.1，2026-10），有 `icqq-windows.tar.gz` |

### 10.2 方案 A：LuckyLilliaBot（原 LLOneBot，**已装进本目录**）

这是 LLOneBot 的延续，同一个团队换了个名字。本工作区已经放好了：`LuckyLillia-Desktop-win-x64\`（Desktop 3.1.2 / 内核 LLBot 8.3.2）。

**已经替你做的事**：

| 项 | 状态 |
| --- | --- |
| `bin\llbot\default_config.json` | 已把 `ob11.connect` 的 `ws-reverse` 设为**启用**，地址 `ws://127.0.0.1:8080/onebot/v11/ws` |
| `setupll.py` | 接线脚本：自动修 `data\config_<QQ号>.json`，登录后跑一次即可（带 .bak 备份） |
| `启动全部.bat` | 一键：生成 config.json → 跑 setupll → 起 bot → 起 LuckyLillia |
| 端口 | 8080（bot）/ 3080（LL WebUI）/ 3001·3000 均空闲 |

**还缺一步：QQ 本体。** LuckyLillia 不自带 QQ，必须装 QQ NT 才能登录。它第一次启动会自己尝试下载，也可以手动装：

```
https://qqdl.gtimg.cn/qqfile/QQNT/9.9.33/beta/8cf72bb8/QQ9.9.33.51552_x64.exe
```

> 版本对上很重要。LuckyLillia 的版本检查针对特定 QQ NT 版本，装完把 QQ 的**自动更新关掉**。

**接线原理**（万一你要自己配）：

```json
"ob11": {
  "enable": true,
  "connect": [
    { "type": "ws-reverse", "enable": true,
      "url": "ws://127.0.0.1:8080/onebot/v11/ws",
      "messageFormat": "array", "heartInterval": 60000 }
  ]
}
```

- `messageFormat: "array"` 必须——bot.py 按消息段数组解析，CQ 码字符串模式不认。
- 配置实际文件是 `data\config_<QQ号>.json`（`DATA_DIR = path.resolve("data")`），QQ 号登录后才生成；`default_config.json` 是生成它的模板。
- 改完 LuckyLillia 会热加载（它 watch 这个文件），不用重启。

**顺序不能反**：先 `python bot.py`（把 8080 听着）→ 再开 LuckyLillia 扫码。反了的话 LL 连不上，虽然它会重连，但日志里一片红。

### 10.3 方案 B：icqq-onebot（Rust，轻，但要配签名）

1. releases 页下 `icqq-windows.tar.gz`，解压
2. `config.example.yaml` 改名 `config.yaml`
3. 填账号 + **`login.sign_api_url`**（NT 在线登录需要签名服务，这一步比 NapCat 麻烦）
4. 在 `onebot` 段里开 `ws_reverse`，地址同样指 `ws://127.0.0.1:8080/onebot/v11/ws`
5. `icqq-onebot.exe` 启动

适合：不想装 QQ 客户端、想要一个几 MB 的单文件。不适合：第一次搭、不想折腾签名。

### 10.4 方案 C：找 NapCat 的旧包

- Docker 用户：以前拉过的镜像还在本地（`docker images | findstr napcat`），能继续跑
- 各种 fork / 镜像仓库 / 网盘转存里通常还有 `NapCat.Shell.Windows.OneKey.zip`
- 关键词：`NapCat OneKey`、`napcat win 版 备份`、`napcat docker`

拿到之后**存一份在本机**，别指望它一直在 GitHub 上。

### 10.5 三条通用建议

1. **别把协议端和 bot 写死在一起**。本项目就是分离的：协议端挂了，换一个，bot 的人格和好感度一条都不丢。
2. **协议端装好先备份整个目录**，尤其是登录后的配置和数据，重装要重新扫码。
3. **先本地跑通再挂 QQ**。用 `console.py`（下一节）验证人格和 API 都正常，再去折腾协议端——不然出问题分不清是谁的锅。

---

## 11. 不接 QQ 也能先玩：console.py

协议端装不上、或者只想先看看她说话对不对味，用本地终端版：

```powershell
python console.py            # 以主人身份私聊
python console.py 22222      # 以 QQ 22222 的同学身份
```

走的是**同一套管道**（persona + worldbook + 好感度 + state.json），所以在这里调顺了，接上 QQ 效果一模一样。

| 命令 | 作用 |
| --- | --- |
| `/group` | 切群聊场合，回复立刻变短变犹豫 |
| `/private` | 切回私聊 |
| `/stage` | 看当前阶段 |
| `/set 4` | 改阶段测试 |
| `/who` | 看她现在把你当谁 |
| `/quit` | 退出并存盘 |

同一个 QQ 号在 console 里聊出来的阶段，接上 QQ 之后会接着算——因为都存在 `state.json` 里。

---

*角色与设定版权归 Nintendo / Intelligent Systems 所有，本项目仅用于个人娱乐。*
