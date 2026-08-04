#!/usr/bin/env python3
"""生成「同步提醒队列v2」—— 新增 + 删除一体的完整 worker。

替换策略：**不改旧 worker**。用户把 5 条自动化重指到 v2，旧的留作备份。
（苹果的自动化绑定的是快捷指令对象而非名字，同名导入骗不过它们 ——
所以「整体替换」必然要重指自动化；v2 用不同名字，选的时候不会选错。）

结构（删除段在前，改期 = 先删后加的顺序由此保证）：

    ① POST /claim-delete       认领待删清单
    ② 重复每一项：
         取 title
         如果 title 有值：          ← 保险丝，见下
             在「提醒事项」列表查找同名 → 移除
    ③ POST /claim              认领待新增清单（与用户旧 worker 同一端点同一鉴权）
    ④ 重复每一项：
         取 title / due / notes
         从 due 解析日期
         添加到「提醒事项」列表（带日期、备注）

删除段走 `/claim?op=delete` —— 生产端 2026-08-04 已部署验证，拿来即用。
（`/claim-delete` 路径别名仍值得日后补上：万一服务回滚到不认识 `op` 的版本，
查询参数会退化成普通 `/claim` 吃掉新增队列，路径形式则只是 404。**但补别名
不阻塞任何事**，与队列源码入库一起办即可。）

**「如果 title 有值」这层保险丝很重要**：删除端点返回非预期内容时
（如错误体） —— 快捷指令的「获取URL内容」**不把 HTTP 错误码当失败**，
会把错误体传进循环。没有保险丝，空 title 进「查找提醒事项」的行为不可预期，
最坏是匹配到整列表并全部删除。有保险丝则任何非预期返回都只是跳过删除段。

产出文件必须先在 Mac 上 `shortcuts sign --mode anyone` 签名才能导入
（iOS 15+ 拒绝未签名文件）。

用法：
    python3 提醒事项/build_worker_v2.py [输出路径] [token]
    token 不传则写占位符（仓库里永远只有占位符）。
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from build_delete_shortcut import (  # noqa: E402
    HOST, LIST_NAME, TOKEN_PLACEHOLDER, _comment, _output, _text,
    _text_var, _var,
)

# 固定 UUID，重新生成时保持稳定
U_DEL_URL = "B2000000-0000-4000-8000-000000000001"
U_DEL_TITLE = "B2000000-0000-4000-8000-000000000002"
U_DEL_FIND = "B2000000-0000-4000-8000-000000000003"
G_DEL_REPEAT = "B2000000-0000-4000-8000-000000000004"
G_DEL_IF = "B2000000-0000-4000-8000-000000000005"
U_ADD_URL = "B2000000-0000-4000-8000-000000000006"
U_ADD_TITLE = "B2000000-0000-4000-8000-000000000007"
U_ADD_DUE = "B2000000-0000-4000-8000-000000000008"
U_ADD_NOTES = "B2000000-0000-4000-8000-000000000009"
U_ADD_DATE = "B2000000-0000-4000-8000-00000000000A"
G_ADD_REPEAT = "B2000000-0000-4000-8000-00000000000B"


def _claim(uuid_: str, path: str, token: str) -> dict:
    """POST <path>，带 X-Queue-Token —— 与用户现有 worker 的请求完全同构。"""
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "UUID": uuid_,
            "WFURL": f"http://{HOST}{path}",
            "WFHTTPMethod": "POST",
            "ShowHeaders": True,
            "WFHTTPHeaders": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {"WFItemType": 0,
                         "WFKey": _text("X-Queue-Token"),
                         "WFValue": _text(token)},
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
        },
    }


def _get_key(uuid_: str, key: str) -> dict:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "UUID": uuid_,
            "WFDictionaryKey": key,
            "WFGetDictionaryValueType": "Value",
            "WFInput": _var("Repeat Item"),
        },
    }


def build(token: str = TOKEN_PLACEHOLDER) -> dict:
    actions = [
        _comment(
            "同步提醒队列v2 —— 新增 + 删除一体\n\n"
            "把 5 条自动化重指到本指令即可；旧的「同步提醒队列」留作备份，不要删。\n"
            "服务器没就绪时删除段自动跳过，新增照常。"
        ),

        # ═══ 删除段 ═══
        _claim(U_DEL_URL, "/claim?op=delete", token),  # 生产端已上线的写法，零等待
        {   # 重复（开始）
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_REPEAT,
                "WFControlFlowMode": 0,
                "WFInput": _output(U_DEL_URL, "Contents of URL"),
            },
        },
        _get_key(U_DEL_TITLE, "title"),
        {   # 如果 title 有值（开始）—— 保险丝
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_IF,
                "WFControlFlowMode": 0,
                "WFCondition": 100,  # 100 = 有任何值
                "WFInput": {
                    "Type": "Variable",
                    "Variable": _output(U_DEL_TITLE, "Dictionary Value"),
                },
            },
        },
        {   # 查找：列表=提醒事项 且 名称=title
            "WFWorkflowActionIdentifier": "is.workflow.actions.filter.reminders",
            "WFWorkflowActionParameters": {
                "UUID": U_DEL_FIND,
                "WFContentItemFilter": {
                    "Value": {
                        "WFActionParameterFilterPrefix": 1,
                        "WFActionParameterFilterTemplates": [
                            {"Class": "WFRemindersContentItem",
                             "Property": "List", "Operator": 4,
                             "Removable": True, "Unit": 0,
                             "Values": {"List": LIST_NAME}},
                            {"Class": "WFRemindersContentItem",
                             "Property": "Name", "Operator": 4,
                             "Removable": True, "Unit": 0,
                             "Values": {"Name": _text_var(U_DEL_TITLE,
                                                          "Dictionary Value")}},
                        ],
                    },
                    "WFSerializationType": "WFContentPredicateTableTemplate",
                },
            },
        },
        {   # 移除
            "WFWorkflowActionIdentifier": "is.workflow.actions.removereminders",
            "WFWorkflowActionParameters": {
                "WFInput": _output(U_DEL_FIND, "Reminders"),
            },
        },
        {   # 如果（结束）
            "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_IF,
                "WFControlFlowMode": 2,
            },
        },
        {   # 重复（结束）
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_DEL_REPEAT,
                "WFControlFlowMode": 2,
            },
        },

        # ═══ 新增段（语义 = 用户旧 worker 的 9 个操作） ═══
        _claim(U_ADD_URL, "/claim", token),
        {   # 重复（开始）
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_ADD_REPEAT,
                "WFControlFlowMode": 0,
                "WFInput": _output(U_ADD_URL, "Contents of URL"),
            },
        },
        _get_key(U_ADD_TITLE, "title"),
        _get_key(U_ADD_DUE, "due"),
        _get_key(U_ADD_NOTES, "notes"),
        {   # 从 due 解析日期
            "WFWorkflowActionIdentifier": "is.workflow.actions.detect.date",
            "WFWorkflowActionParameters": {
                "UUID": U_ADD_DATE,
                "WFInput": _output(U_ADD_DUE, "Dictionary Value"),
            },
        },
        {   # 添加提醒
            "WFWorkflowActionIdentifier": "is.workflow.actions.addnewreminder",
            "WFWorkflowActionParameters": {
                "WFCalendarItemTitle": _text_var(U_ADD_TITLE, "Dictionary Value"),
                "WFCalendarItemCalendar": LIST_NAME,
                "WFAlertEnabled": True,
                "WFAlertCustomTime": _output(U_ADD_DATE, "Date"),
                "WFCalendarItemNotes": _text_var(U_ADD_NOTES, "Dictionary Value"),
            },
        },
        {   # 重复（结束）
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": G_ADD_REPEAT,
                "WFControlFlowMode": 2,
            },
        },
    ]

    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 431817727,
            "WFWorkflowIconGlyphNumber": 59511,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["NCWidget", "WatchKit"],
        "WFWorkflowInputContentItemClasses": [
            "WFAppStoreAppContentItem", "WFArticleContentItem",
            "WFContactContentItem", "WFDateContentItem",
            "WFEmailAddressContentItem", "WFFolderContentItem",
            "WFGenericFileContentItem", "WFImageContentItem",
            "WFiTunesProductContentItem", "WFLocationContentItem",
            "WFDCMapsLinkContentItem", "WFAVAssetContentItem",
            "WFPDFContentItem", "WFPhoneNumberContentItem",
            "WFRichTextContentItem", "WFSafariWebPageContentItem",
            "WFStringContentItem", "WFURLContentItem",
        ],
        "WFQuickActionSurfaces": [],
        "WFWorkflowHasOutputParameters": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowActions": actions,
    }


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path("同步提醒队列v2.shortcut")
    token = argv[2] if len(argv) > 2 else TOKEN_PLACEHOLDER
    data = plistlib.dumps(build(token), fmt=plistlib.FMT_XML)
    out.write_bytes(data)
    back = plistlib.loads(out.read_bytes())
    ids = [a["WFWorkflowActionIdentifier"] for a in back["WFWorkflowActions"]]
    assert ids == [a["WFWorkflowActionIdentifier"]
                   for a in build()["WFWorkflowActions"]]
    # 结构自检：控制流配对完整
    flows = [a["WFWorkflowActionParameters"].get("WFControlFlowMode")
             for a in back["WFWorkflowActions"]
             if "WFControlFlowMode" in a["WFWorkflowActionParameters"]]
    assert flows.count(0) == flows.count(2) == 3, flows  # 2×重复 + 1×如果
    print(f"✅ {out}  ({out.stat().st_size} 字节, {len(ids)} 个动作, 控制流配对 OK)")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main(sys.argv))
