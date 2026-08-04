#!/usr/bin/env python3
"""把「删除段」拼到用户**真实导出**的 worker 前面，产出新增+删除一体的 v2。

**为什么是拼接而不是重写**：2026-08-04 整段重写失败了 —— 我凭截图猜苹果的
内部参数名，`WFCalendarItemTitle` 用错了序列化方式（文本字段必须
`WFTextTokenString` + `attachmentsByRange`，我用成了 `WFTextTokenAttachment`），
运行时报「未提供标题」。列表名也猜错过（界面上的蓝字「提醒事项」是 App 名，
真实列表是 `收集桶`）。

结论写进铁律：**能拿到用户的真实文件时，绝不重造能跑的部分。**
新增段逐字节沿用，风险面只剩新写的删除段。

输入接受两种形式：
  * 苹果导出的**已签名** `.shortcut`（AEA1 容器）—— 自动解出内嵌 plist
  * 未签名的 plist（bplist 或 XML）

用法：
    python3 提醒事项/splice_delete_block.py 输入.shortcut 输出.shortcut

产出仍需在 Mac 上签名才能导入：
    shortcuts sign --mode anyone --input 输出.shortcut --output 已签名.shortcut
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

# 删除段的 UUID —— 与用户文件里的 UUID 不会撞（那些是苹果随机生成的）
U_DEL_URL = "C3000000-0000-4000-8000-000000000001"
U_DEL_TITLE = "C3000000-0000-4000-8000-000000000002"
U_DEL_FIND = "C3000000-0000-4000-8000-000000000003"
G_DEL_REPEAT = "C3000000-0000-4000-8000-000000000004"
G_DEL_IF = "C3000000-0000-4000-8000-000000000005"


def load_shortcut(path: Path) -> dict:
    """读已签名(AEA)或未签名的快捷指令，返回 plist 字典。"""
    raw = path.read_bytes()
    try:
        return plistlib.loads(raw)
    except Exception:
        pass
    if raw[:4] != b"AEA1":
        raise SystemExit(f"❌ 无法识别的文件格式：{raw[:8]!r}")
    # AEA profile 0 = 只签名不加密；正文为 LZFSE，内嵌 plist 前有一段头部
    try:
        import lzfse
    except ImportError:
        raise SystemExit("❌ 需要 lzfse 才能读已签名文件：pip install lzfse")
    end = raw.find(b"bvxn", 12)
    if end < 0:
        raise SystemExit("❌ 签名容器里找不到 LZFSE 数据")
    blob = lzfse.decompress(raw[end:])
    i = blob.find(b"bplist00")
    if i < 0:
        i = blob.find(b"<?xml")
    if i < 0:
        raise SystemExit("❌ 解压后找不到 plist")
    return plistlib.loads(blob[i:])


def _text(s: str) -> dict:
    return {"Value": {"string": s}, "WFSerializationType": "WFTextTokenString"}


def _attach(uuid_: str, name: str) -> dict:
    """整值字段（WFInput 之类）引用某一步的输出。"""
    return {
        "Value": {"OutputUUID": uuid_, "Type": "ActionOutput", "OutputName": name},
        "WFSerializationType": "WFTextTokenAttachment",
    }


def _text_var(uuid_: str, name: str) -> dict:
    """**文本字段**里嵌变量 —— 与整值字段不是一回事，用错不绑定且不报错。

    格式取自用户真实 worker 的 `WFCalendarItemTitle`（苹果自己生成的）。
    """
    return {
        "Value": {
            "string": "￼",
            "attachmentsByRange": {
                "{0, 1}": {"OutputUUID": uuid_, "Type": "ActionOutput",
                           "OutputName": name},
            },
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _var(name: str) -> dict:
    return {
        "Value": {"VariableName": name, "Type": "Variable"},
        "WFSerializationType": "WFTextTokenAttachment",
    }


def extract_token(actions: list[dict]) -> tuple[str, str]:
    """从用户文件里读出 URL 与 X-Queue-Token —— 不让用户手填，也不进仓库。"""
    for a in actions:
        if a["WFWorkflowActionIdentifier"] != "is.workflow.actions.downloadurl":
            continue
        p = a["WFWorkflowActionParameters"]
        url = p.get("WFURL", "")
        for item in (p.get("WFHTTPHeaders", {}).get("Value", {})
                     .get("WFDictionaryFieldValueItems", [])):
            if item.get("WFKey", {}).get("Value", {}).get("string") == "X-Queue-Token":
                return url, item["WFValue"]["Value"]["string"]
        return url, ""
    raise SystemExit("❌ 输入文件里找不到「获取URL内容」动作")


def delete_block(claim_url: str, token: str, list_name: str) -> list[dict]:
    """认领待删清单 → 逐条在指定列表里按标题精确查找 → 移除。

    「如果 有任何值」是保险丝：端点返回非预期内容（错误体）时，空标题不会
    进入查找动作 —— 否则最坏情况是匹配到整个列表并全部删除。
    """
    return [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
            "WFWorkflowActionParameters": {"WFCommentActionText":
                "▼ 删除段（新增段在下面，与原版逐字节一致）\n"
                "认领待删清单 → 在「%s」里按标题精确查找 → 移除。\n"
                "删除必须跑在新增之前：改期＝先删后加。" % list_name},
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": U_DEL_URL,
                "WFURL": claim_url,
                "WFHTTPMethod": "POST",
                "ShowHeaders": True,
                "WFHTTPHeaders": {
                    "Value": {"WFDictionaryFieldValueItems": [{
                        "WFItemType": 0,
                        "WFKey": _text("X-Queue-Token"),
                        "WFValue": _text(token),
                    }]},
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_REPEAT,
                "WFControlFlowMode": 0,
                "WFInput": _attach(U_DEL_URL, "Contents of URL"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": U_DEL_TITLE,
                "WFDictionaryKey": "title",
                "WFInput": _var("Repeat Item"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_IF,
                "WFControlFlowMode": 0,
                "WFCondition": 100,          # 100 = 有任何值
                # 条件动作的输入必须再包一层 {Type:"Variable", Variable:…}，
                # 直接给 attachment 是静默失效（2026-08-04 格式调研证实）
                "WFInput": {"Type": "Variable",
                            "Variable": _attach(U_DEL_TITLE, "Dictionary Value")},
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.filter.reminders",
            "WFWorkflowActionParameters": {
                "UUID": U_DEL_FIND,
                # 行内 Values 按**值类型**键入（String/Enumeration/…），不是按属性名；
                # 列表是 Enumeration + WFStringSubstitutableState；标题属性现代系统叫
                # Title（旧系统叫 Name）。三点均为 2026-08-04 格式调研结论。
                "WFContentItemFilter": {
                    "Value": {
                        "WFActionParameterFilterPrefix": 1,   # 全部满足
                        "WFContentPredicateBoundedDate": False,
                        "WFActionParameterFilterTemplates": [
                            {"Class": "WFRemindersContentItem", "Property": "List",
                             "Operator": 4, "Removable": True,
                             "Values": {"Enumeration": {
                                 "Value": list_name,
                                 "WFSerializationType": "WFStringSubstitutableState"}}},
                            {"Class": "WFRemindersContentItem", "Property": "Title",
                             "Operator": 4, "Removable": True,
                             "Values": {"String": _text_var(U_DEL_TITLE,
                                                            "Dictionary Value")}},
                        ],
                    },
                    "WFSerializationType": "WFContentPredicateTableTemplate",
                },
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.removereminders",
            "WFWorkflowActionParameters": {
                # 键名是 WFInputReminders（同族：Remove Events 用 WFInputEvents）。
                # 用 WFInput 则查找结果根本传不进来 —— 静默失效。
                # 另：此动作运行时**必弹系统确认框**，无参数可关，属预期行为。
                "WFInputReminders": _attach(U_DEL_FIND, "Reminders"),
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_IF, "WFControlFlowMode": 2},
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_REPEAT, "WFControlFlowMode": 2},
        },
    ]


def find_list_name(actions: list[dict]) -> str:
    """从用户的「添加新提醒事项」动作里读出真实列表名 —— 不猜。"""
    for a in actions:
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.addnewreminder":
            p = a["WFWorkflowActionParameters"]
            d = p.get("WFCalendarDescriptor")
            if isinstance(d, dict) and d.get("Title"):
                return str(d["Title"])
            if p.get("WFCalendarItemCalendar"):
                return str(p["WFCalendarItemCalendar"])
    raise SystemExit("❌ 读不出列表名，拒绝猜测")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    doc = load_shortcut(src)
    actions = doc["WFWorkflowActions"]

    claim_url, token = extract_token(actions)
    if not token:
        raise SystemExit("❌ 原文件里没有 X-Queue-Token，拒绝产出半成品")
    list_name = find_list_name(actions)
    delete_url = claim_url.replace("/claim", "/claim?op=delete", 1)

    doc["WFWorkflowActions"] = delete_block(delete_url, token, list_name) + actions
    dst.write_bytes(plistlib.dumps(doc, fmt=plistlib.FMT_BINARY))

    back = plistlib.loads(dst.read_bytes())
    new = back["WFWorkflowActions"]
    assert new[len(new) - len(actions):] == actions, "新增段被改动了 —— 不该发生"
    flows = [a["WFWorkflowActionParameters"].get("WFControlFlowMode")
             for a in new if "WFControlFlowMode" in a["WFWorkflowActionParameters"]]
    assert flows.count(0) == flows.count(2), f"控制流未配对: {flows}"

    print(f"✅ {dst}  ({dst.stat().st_size} 字节)")
    print(f"   列表名（读自原文件）：{list_name}")
    print(f"   删除端点：{delete_url}")
    print(f"   动作数：{len(actions)} → {len(new)}（新增段逐字节保留 ✅）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
