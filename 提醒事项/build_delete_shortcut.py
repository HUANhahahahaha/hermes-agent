#!/usr/bin/env python3
"""生成「提醒事项 · 删除」快捷指令文件（.shortcut，XML plist）。

**为什么单独做一个快捷指令，而不是改现有那个**：现有那个是唯一送达通道，
改坏了新增也一起废。两个快捷指令各自由「自动化」定时触发，互不相干 ——
这个就算完全是坏的，也伤不到新增。

它做的事：
    GET  http://<HOST>/claim-delete        认领待删清单（认领即出队）
    重复每一项：
        取 title → 在「收集桶」里查找同名提醒 → 移除

**端点是独立路径 `/claim-delete`，不是 `/claim?op=delete`。**
认不出的查询参数会被服务器静默忽略、退化成普通 `/claim`，于是删除段会拿着
**待新增**的那批条目去查找并移除 —— 排队等着进手机的提醒被吃掉，且一条没加。
认不出的**路径**返回 404，这一段空转，无害。所以本快捷指令可以先于服务器安装。

用法：
    python3 提醒事项/build_delete_shortcut.py [输出路径]
"""
from __future__ import annotations

import plistlib
import sys
import uuid
from pathlib import Path

HOST = "43.160.249.235:8787"
LIST_NAME = "收集桶"
TOKEN_PLACEHOLDER = "在这里粘贴你的队列 token"

# 固定 UUID：重新生成同一份文件时保持稳定，便于 diff。
U_URL = "A1000000-0000-4000-8000-000000000001"
U_ITEM = "A1000000-0000-4000-8000-000000000002"
U_FIND = "A1000000-0000-4000-8000-000000000003"
U_REPEAT = "A1000000-0000-4000-8000-000000000004"


def _text(s: str) -> dict:
    """字面量字符串参数。"""
    return {"Value": {"string": s}, "WFSerializationType": "WFTextTokenString"}


def _output(uuid_: str, name: str) -> dict:
    """引用某一步的输出。"""
    return {
        "Value": {"OutputName": name, "OutputUUID": uuid_, "Type": "ActionOutput"},
        "WFSerializationType": "WFTextTokenAttachment",
    }


def _var(name: str) -> dict:
    """引用魔法变量（内部名固定用英文，中文界面上显示为「重复项目」）。"""
    return {
        "Value": {"Type": "Variable", "VariableName": name},
        "WFSerializationType": "WFTextTokenAttachment",
    }


def _comment(text: str) -> dict:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.comment",
        "WFWorkflowActionParameters": {"WFCommentActionText": text},
    }


def build() -> dict:
    actions = [
        _comment(
            "提醒事项 · 删除\n\n"
            "① 先把下面「获取URL内容」里的 X-Queue-Token 换成真值 —— "
            "打开你现有那个新增用的快捷指令，从它的标头里复制。\n"
            "② 建一条「自动化」定时触发本快捷指令，间隔与新增那条保持一致。\n"
            "③ 服务器还没加 /claim-delete 时，这里会 404，本指令空转，无害。"
        ),
        # ① 认领待删清单
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": U_URL,
                "WFURL": f"http://{HOST}/claim-delete",
                "WFHTTPMethod": "GET",
                "ShowHeaders": True,
                "WFHTTPHeaders": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            {
                                "WFItemType": 0,
                                "WFKey": _text("X-Queue-Token"),
                                "WFValue": _text(TOKEN_PLACEHOLDER),
                            }
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
            },
        },
        # ② 重复每一项（开始）
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": U_REPEAT,
                "WFControlFlowMode": 0,
                "WFInput": _output(U_URL, "Contents of URL"),
            },
        },
        # ③ 取出 title
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": U_ITEM,
                "WFDictionaryKey": "title",
                "WFGetDictionaryValueType": "Value",
                "WFInput": _var("Repeat Item"),
            },
        },
        # ④ 在「收集桶」里按标题精确查找
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.filter.reminders",
            "WFWorkflowActionParameters": {
                "UUID": U_FIND,
                "WFContentItemFilter": {
                    "Value": {
                        "WFActionParameterFilterPrefix": 1,   # 1 = 全部满足
                        "WFActionParameterFilterTemplates": [
                            {
                                "Class": "WFRemindersContentItem",
                                "Property": "List",
                                "Operator": 4,               # 4 = 是
                                "Removable": True,
                                "Unit": 0,
                                "Values": {"List": LIST_NAME},
                            },
                            {
                                "Class": "WFRemindersContentItem",
                                "Property": "Name",
                                "Operator": 4,               # 4 = 是
                                "Removable": True,
                                "Unit": 0,
                                "Values": {"Name": _output(U_ITEM, "Dictionary Value")},
                            },
                        ],
                    },
                    "WFSerializationType": "WFContentPredicateTableTemplate",
                },
            },
        },
        # ⑤ 移除
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.removereminders",
            "WFWorkflowActionParameters": {
                "WFInput": _output(U_FIND, "Reminders"),
            },
        },
        # ⑥ 重复（结束）
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
            "WFWorkflowActionParameters": {
                "GroupingIdentifier": U_REPEAT,
                "WFControlFlowMode": 2,
            },
        },
    ]

    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4274264319,
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
    out = Path(argv[1]) if len(argv) > 1 else Path("提醒事项-删除.shortcut")
    data = plistlib.dumps(build(), fmt=plistlib.FMT_XML)
    out.write_bytes(data)
    # 回读校验：写出来的东西必须还能解析回同样的动作序列
    back = plistlib.loads(out.read_bytes())
    ids = [a["WFWorkflowActionIdentifier"] for a in back["WFWorkflowActions"]]
    assert ids == [a["WFWorkflowActionIdentifier"] for a in build()["WFWorkflowActions"]]
    print(f"✅ {out}  ({out.stat().st_size} 字节, {len(ids)} 个动作)")
    for i, a in enumerate(ids, 1):
        print(f"   {i}. {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
