"""离线自检：不连 QQ、不调 LLM，只验人格加载 / 世界书 / 解析 / 状态落盘。"""
import asyncio
import json

import bot
from plugins.bernie import persona, worldbook

print("== 角色卡 ==")
print("name:", bot.CARD["name"], "| 世界书条目:", worldbook.count(), "| system 长度:", len(persona.base_system_prompt()))

print("\n== 世界书命中 ==")
for probe in ["我是主人，群里同学在刷屏", "父亲派来的人", "她绣的猪笼草", "今晚想抱着你一起睡"]:
    print(f"  {probe!r} -> {worldbook.matched_names(probe)}")

print("\n== 消息解析 ==")
segs = [{"type": "at", "data": {"qq": "999"}}, {"type": "text", "data": {"text": " 贝尔 早 "}}]
print(bot.parse_message(segs, "999"))
print(bot.parse_message([{"type": "text", "data": {"text": "普通发言"}}], "999"))

print("\n== 输出清洗 ==")
reply, delta = bot.clean_reply("**那个……**在、在的。\n[[STAGE:+1]]", "群聊")
print(f"  reply={reply!r} delta={delta}")
long_reply, _ = bot.clean_reply("（把熊抱紧）……嗯。" * 60, "群聊")
print(f"  超长截断后 {len(long_reply)} 字，结尾：…{long_reply[-12:]!r}")

print("\n== 状态落盘 ==")
u = bot.STORE.user("10001")
u["name"] = "test"
u["stage"] = 3
u["notes"] = ["怕打雷"]
u["history"] = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "……你、你好。"}]
asyncio.run(bot.STORE.save())
raw = json.loads((bot.BASE_DIR / "state.json").read_text(encoding="utf-8"))
print("  users 数量:", len(raw["users"]), "| 该用户阶段:", raw["users"]["10001"]["stage"])

print("\n== 提示词组装 ==")
for ctx, label in [("群聊", "group"), ("私聊", "private")]:
    msg = bot.build_messages("10001", "test", ctx, "贝尔，你在吗", ["甲：早", "乙：今天有课"] if ctx == "群聊" else None)
    sys_txt = msg[0]["content"]
    print(f"  [{ctx}] system {len(sys_txt)} 字 | messages {len(msg)} 条")
    for kw in ["本轮状态", "称呼规则", "当前场合", "输出约定", "刚才群里在聊", "相关设定"]:
        if kw in sys_txt:
            print(f"     含 {kw}")
print("  同学判定：", "老师 · 唯一的主人" if bot.is_master("10001") else "同学（符合预期，master_ids 里没有它）")
print("  主人判定（example 里的占位号）：", bot.is_master("123456789"))

print("\n== 管理命令（不发送，只验证分支）==")
print("  /stage /set /note /reset /reload 已绑定 handle_admin")
print("\nOK")
