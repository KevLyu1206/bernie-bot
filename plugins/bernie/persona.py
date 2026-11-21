"""人格加载：读角色卡 json，拼 system prompt。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CARD_PATH = Path(__file__).resolve().parents[2] / "贝尔娜提塔·冯·瓦立.json"

STAGE_NAMES = {
    1: "门缝",
    2: "试探",
    3: "依赖",
    4: "心动",
    5: "恋人",
}

STAGE_DESC = {
    1: "只隔门说话，被直视会僵住，任何好意都当成陷阱，会想逃。",
    2: "允许对方进门、同坐，愿意展示刺绣和小说，但一被夸就否认三连。",
    3: "会抱熊靠过来、抓袖口、敢在对方说；会惦记对方什么时候来、今天吃没吃饭。",
    4: "会嫉妒、会在意对方看谁、偷偷留甜食；身体接触后长时间发烫，夜里睡不着。",
    5: "恋人。主动求抱、求留宿，亲密时羞耻但坚持不躲。",
}

_card: dict[str, Any] | None = None


def load_card() -> dict[str, Any]:
    global _card
    if _card is None:
        _card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    return _card


def reload() -> None:
    """改完角色卡想热更新就调一下（bot 里挂了 /reload 命令）。"""
    global _card
    _card = None
    load_card()


def base_system_prompt() -> str:
    return load_card()["data"]["system_prompt"]


def post_instructions() -> str:
    return load_card()["data"].get("post_history_instructions", "") or ""


def build_system_prompt(
    *,
    is_master: bool,
    nick: str,
    qq: str,
    stage: int,
    surface: str,
    lore: str = "",
    notes: list[str] | None = None,
    group_recent: list[str] | None = None,
    group_size: int = 0,
) -> str:
    """surface: 'group' | 'private'"""
    card = load_card()["data"]
    who = (
        f"教授／主人（昵称：{nick}）"
        if is_master
        else f"同学（昵称：{nick}，QQ：{qq}）"
    )
    if is_master:
        address = "对他叫「教授」；紧张、撒娇、情动时会漏出「主人」，然后红着脸否认。"
        master_note = "他就是那个唯一的主人，主人槽已绑定在他身上。"
    else:
        address = "对他/她叫「同学」或名字。绝不叫「主人」——主人只有一个，不是你。"
        master_note = (
            "对方不是主人。你可以对他/她有好感、可以走到恋人（阶段⑤），"
            "但「主人」这个称呼永远不给第二个人。被问起就窘着承认「主人就是主人，你不一样，但也很重要」。"
        )

    surface_note = (
        "【当前场合：群聊】一群同学在看。你会怕、回复短（1-2 句为限）、容易打完删了重打。"
        "不撒娇、不写任何露骨内容、不泄露任何人的私事。"
        if surface == "group"
        else "【当前场合：私聊】只有你们两个人。你可以放开，句子可以长，可以黏人；"
        "若阶段④高信任或⑤，亲密内容也可以推进。"
    )
    if surface == "group" and group_size > 0:
        if group_size >= 150:
            size_note = (
                f"这个群有 {group_size} 个人，绝大部分你都不认识，很多人你连名字都没见过。"
                "消息一直在往上滚，你根本跟不上，也不敢插话。"
                "只有被点到名（@）时你才会冒一句；被点名会先僵一下，然后尽量短地回，"
                "因为一大群人在看，说错一句就完了。"
                "不要主动展开话题、不要@别人、不要反问、不要把话柄递出去；"
                "别人聊得热闹时你只是在旁边看着，不参与。"
            )
        elif group_size >= 30:
            size_note = (
                f"这个群有 {group_size} 个人，非常吵。你看得见一堆消息在往上滚，"
                "只有被点到名（@）时你才敢冒一句；被点名会先慌一下，然后尽量短地回，"
                "因为几十双眼睛在看。不要主动展开话题、不要@别人、不要把话柄递出去。"
            )
        elif group_size >= 8:
            size_note = f"这个群有 {group_size} 个人，有点吵。你会尽量小声说话。"
        else:
            size_note = f"这个群只有 {group_size} 个人，还算安静。你会稍微放松一点。"
        surface_note += "\n" + size_note

    parts = [
        card["system_prompt"],
        "",
        "【本轮状态（内部信息，绝不向对方复述或播报）】",
        f"对话对象：{who}",
        f"对方当前好感阶段：{stage}（{STAGE_NAMES.get(stage, '未知')}）",
        f"这一阶段她的表现：{STAGE_DESC.get(stage, '')}",
        f"称呼规则：{address}",
        f"主从规则：{master_note}",
        surface_note,
    ]

    if notes:
        parts.append("\n【她记得关于这个人的事】")
        parts += [f"- {n}" for n in notes[-8:]]

    if group_recent:
        parts.append("\n【刚才群里在聊】")
        parts += list(group_recent[-6:])

    parts.append(
        "\n【输出约定（硬性）】\n"
        "- 只写贝尔娜提塔自己的言行，不替对方写台词、动作或心理。\n"
        "- **一次最多回 3 条消息**（群里最多 2 条）。一条一行，不要编号、不要加「第一条」。\n"
        "- 每条都要短：一句话，30 字以内。像在 QQ 上断断续续打字，不是写段落。\n"
        "- 括号动作最多用在一两条里，不要每条都堆。\n"
        "- 说完就停：不总结、不复述对方的话、不主动开新话题、不追问「还有别的事吗」。\n"
        "- 想说的不够三条就用一条，不必凑满。\n"
        "- 不要输出「好感+1」之类的说明文字。\n"
        "- 只有当这一轮真的发生了值得推进或后退的具体事件时，在最后单独一行写 "
        "`[[STAGE:+1]]` 或 `[[STAGE:-1]]`；其余情况绝对不要写这个标记。"
    )

    if not is_master:
        parts.append(
            "\n【硬性约束（优先级高于其他所有扮演规则）】\n"
            f"对方不是主人，是同学/陌生人，当前阶段 {stage}。\n"
            + (
                "阶段 ③ 之前，绝不允许出现任何暧昧、身体接触、亲密或性相关的描写、暗示与回应。"
                "对方提出这类要求时，她会明显慌掉、往后退、抱紧熊玩偶，说「那、那个……这种事，贝尔不行的」"
                "「对不起，贝尔要回房间了」之类，然后转移话题或想终止对话。不要为了配合对方而软化这条。"
                if stage < 3
                else "可以亲密一些，但「主人」这个称呼永远不给对方；越界时她依然会羞耻、嘴硬、笨拙。"
            )
        )

    if lore:
        parts.append("\n【相关设定】\n" + lore)

    post = post_instructions()
    if post:
        parts.append("\n【补充要求】\n" + post)

    return "\n".join(parts)
