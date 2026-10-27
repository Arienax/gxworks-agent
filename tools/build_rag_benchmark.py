#!/usr/bin/env python3
# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Build a deterministic, structured FX3U RAG retrieval benchmark."""

from __future__ import annotations

import sys as _private_sys
from pathlib import Path as _PrivatePath
_private_sys.path.insert(0, str(_PrivatePath(__file__).resolve().parents[1]))
from tools.offline_inputs import private_path

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


CURATED_DEVICES = [
    "X000", "X001", "Y000", "Y001", "M8000", "M8002", "M8029",
    "M8122", "M8123", "D8000", "D8067", "D8120", "D8336", "C1",
    "C251", "T0", "S1", "V0", "Z0", "P63",
]

CURATED_MOTION_CASES = [
    {
        "case_id": "motion_instruction_drvi",
        "query": "FX3U DRVI 相对定位的 S1、S2、D1、D2 各是什么，方向输出如何指定？",
        "category": "motion_instruction",
        "task_type": "generate",
        "expected_entities": ["DRVI"],
        "expected_chunk_types": ["instruction"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_instruction_drva",
        "query": "FX3U DRVA 绝对定位的目标位置、速度、脉冲输出和方向输出操作数是什么？",
        "category": "motion_instruction",
        "task_type": "generate",
        "expected_entities": ["DRVA"],
        "expected_chunk_types": ["instruction"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_instruction_zrn",
        "query": "FX3U ZRN 的回零速度、爬行速度、DOG 输入和脉冲输出操作数顺序是什么？",
        "category": "motion_instruction",
        "task_type": "generate",
        "expected_entities": ["ZRN"],
        "expected_chunk_types": ["instruction"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_instruction_dszr",
        "query": "FX3U DSZR 的 DOG、零相信号、脉冲输出和方向输出 D2 如何配置？",
        "category": "motion_instruction",
        "task_type": "generate",
        "expected_entities": ["DSZR"],
        "expected_chunk_types": ["instruction"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_instruction_dvit",
        "query": "FX3U DVIT 中断定位如何指定中断输入，M8336 和 D8336 的作用是什么？",
        "category": "motion_instruction",
        "task_type": "analysis",
        "expected_entities": ["DVIT", "M8336"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_device_m8336",
        "query": "M8336 能否作为 ZRN 或 DSZR 原点回归完成标志？它的官方定义是什么？",
        "category": "motion_device",
        "task_type": "program_review",
        "expected_entities": ["M8336"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_device_d8342",
        "query": "FX3U Y0 定位参数 D8342 表示什么速度？",
        "category": "motion_device",
        "task_type": "analysis",
        "expected_entities": ["D8342"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_device_d8343",
        "query": "D8343/D8344 是 16 位还是 32 位定位参数，应该用 MOV 还是 DMOV？",
        "category": "motion_device",
        "task_type": "program_review",
        "expected_entities": ["D8343", "D8344"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_device_d8345",
        "query": "FX3U D8345 是最高速度还是 ZRN/DSZR 的爬行速度？",
        "category": "motion_device",
        "task_type": "program_review",
        "expected_entities": ["D8345"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_device_d8346",
        "query": "FX3U D8346/D8347 原点回归速度的字长和用途是什么？",
        "category": "motion_device",
        "task_type": "analysis",
        "expected_entities": ["D8346", "D8347"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_device_d8348_d8349",
        "query": "FX3U 定位加速时间和减速时间分别是 D8348 还是 D8349？",
        "category": "motion_device",
        "task_type": "analysis",
        "expected_entities": ["D8348", "D8349"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_status_busy",
        "query": "FX3U Y0/Y1/Y2 脉冲输出 BUSY/READY 分别对应 M8340、M8350、M8360 吗？",
        "category": "motion_status",
        "task_type": "debug",
        "expected_entities": ["M8340", "M8350", "M8360"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_status_activation",
        "query": "M8348、M8358、M8368 是定位指令驱动中还是脉冲停止命令？",
        "category": "motion_status",
        "task_type": "program_review",
        "expected_entities": ["M8348", "M8358", "M8368"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_status_stop_command",
        "query": "FX3U M8349/M8359/M8369 是监控位还是用户写入的脉冲停止命令？",
        "category": "motion_status",
        "task_type": "program_review",
        "expected_entities": ["M8349", "M8359", "M8369"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_completion_physical",
        "query": "FX3U M8029 定位指令完成是否等于伺服电机已经机械停止和 INP 到位？",
        "category": "motion_debug",
        "task_type": "debug",
        "expected_entities": ["M8029"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_direction_arbitrary",
        "query": "FX3U 使用 Y0 发定位脉冲时，方向信号是否必须固定为 Y4？可以用 Y7 吗？",
        "category": "motion_wiring",
        "task_type": "program_review",
        "expected_entities": ["Y0", "Y4", "Y7"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_builtin_axes",
        "query": "FX3U 基本单元内置定位有几轴，Y0/Y1/Y2 的最高脉冲频率是多少？",
        "category": "motion_hardware",
        "task_type": "analysis",
        "expected_entities": ["Y0", "Y1", "Y2"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_adapter_y3",
        "query": "FX3U-2HSY-ADP 如何提供 200kHz 和 Y3 脉冲输出，需要几块适配器？",
        "category": "motion_hardware",
        "task_type": "analysis",
        "expected_entities": ["Y3"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_relay_adapter",
        "query": "FX3U 继电器输出型 CPU 能否通过 FX3U-2HSY-ADP 使用 ZRN/DRVI 高速脉冲？",
        "category": "motion_hardware",
        "task_type": "analysis",
        "expected_entities": ["ZRN", "DRVI"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_module_selection",
        "query": "基本单元内置脉冲、FX3U-2HSY-ADP、FX3U-1PG 和 FX2N-10PG 的编程方式能否混用？",
        "category": "motion_hardware",
        "task_type": "analysis",
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_zrn_direction",
        "query": "FX3U ZRN 没有方向输出操作数时，怎样切换原点回归方向并配合 REF？",
        "category": "motion_wiring",
        "task_type": "generate",
        "expected_entities": ["ZRN", "REF"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
    {
        "case_id": "motion_stepper_requirements",
        "query": "FX3U 控制步进电机时，PLSY 与 DRVI 分别需要确认哪些轴、频率、目标脉冲和方向参数？",
        "category": "motion_requirements",
        "task_type": "analysis",
        "expected_entities": ["PLSY", "DRVI"],
        "expected_manual_ids": ["fx3_positioning_k"],
    },
]

NEGATIVE_QUERIES = [
    "请帮我润色这段文字",
    "明天天气怎么样",
    "写一封项目进度邮件",
    "把这个普通 Python 函数改短一些",
    "推荐一款适合旅行的相机",
    "生成一份会议纪要模板",
    "解释一下牛顿第二定律",
    "帮我设计一个餐厅菜单",
    "请检查这份合同的排版",
    "给我一个健身计划",
    "FX5U SM8002 高速脉冲程序",
    "西门子 S7-1200 TIA Portal 定时器",
    "欧姆龙 NJ 系列 EtherCAT 配置",
    "三菱 iQ-R RCPU 冗余系统",
    "Arduino UNO PWM 引脚说明",
    "树莓派 GPIO 输入输出示例",
]


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=private_path('resources/knowledge/fx3u_knowledge.sqlite'),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=private_path('benchmarks/fx3u_rag_benchmark.jsonl'),
    )
    parser.add_argument(
        "--meta-output",
        type=Path,
        default=private_path('benchmarks/fx3u_rag_benchmark.meta.json'),
    )
    parser.add_argument("--target", type=int, default=220)
    args = parser.parse_args()
    if not 100 <= args.target <= 300:
        parser.error("--target must be between 100 and 300")
    return args


def normalized_query(value: str) -> str:
    return " ".join(str(value).casefold().split())


def build_cases(connection: sqlite3.Connection, target: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_queries: set[str] = set()

    def add(
        *,
        case_id: str,
        query: str,
        category: str,
        task_type: str,
        plc_model: str = "FX3U",
        expected_entities: list[str] | None = None,
        expected_chunk_types: list[str] | None = None,
        expected_manual_ids: list[str] | None = None,
        expected_case_ids: list[str] | None = None,
        negative: bool = False,
    ) -> None:
        key = normalized_query(query)
        if not key or key in seen_queries:
            return
        seen_queries.add(key)
        cases.append(
            {
                "id": case_id,
                "query": query,
                "category": category,
                "plc_model": plc_model,
                "task_type": task_type,
                "expected_entities": expected_entities or [],
                "expected_chunk_types": expected_chunk_types or [],
                "expected_manual_ids": expected_manual_ids or [],
                "expected_case_ids": expected_case_ids or [],
                "negative": bool(negative),
            }
        )

    instruction_rows = connection.execute(
        """
        SELECT i.opcode,i.title,i.manual_id,
               (SELECT a.alias FROM instruction_aliases a
                WHERE a.instruction_id=i.id AND a.alias_type='zh_alias'
                ORDER BY length(a.alias) DESC LIMIT 1) AS zh_alias
        FROM instructions i
        JOIN manuals m ON m.manual_id=i.manual_id
        WHERE i.chunk_id IS NOT NULL
        ORDER BY CASE WHEN i.manual_id='fx3_programming_r' THEN 0 ELSE 1 END,
                 m.priority DESC,i.page_start,i.opcode
        """
    ).fetchall()
    selected_opcodes: set[str] = set()
    selected_instructions = []
    for opcode, title, manual_id, zh_alias in instruction_rows:
        normalized_opcode = str(opcode).upper()
        if normalized_opcode in selected_opcodes:
            continue
        selected_opcodes.add(normalized_opcode)
        selected_instructions.append((normalized_opcode, str(title), str(manual_id), zh_alias))
        if len(selected_instructions) >= 40:
            break
    for index, (opcode, title, manual_id, zh_alias) in enumerate(selected_instructions, start=1):
        add(
            case_id=f"instruction_{index:03d}_a",
            query=f"FX3U {opcode} 指令的操作数、适用软元件和主要限制是什么？",
            category="instruction",
            task_type="generate",
            expected_entities=[opcode],
            expected_chunk_types=["instruction"],
            expected_manual_ids=[manual_id],
        )
        label = str(zh_alias or title or opcode).strip()
        add(
            case_id=f"instruction_{index:03d}_b",
            query=f"{label}（{opcode}）执行条件、数据类型和完成标志怎么判断？",
            category="instruction_paraphrase",
            task_type="analysis",
            expected_entities=[opcode],
            expected_chunk_types=["instruction"],
            expected_manual_ids=[manual_id],
        )

    for index, device in enumerate(CURATED_DEVICES, start=1):
        add(
            case_id=f"device_{index:03d}",
            query=f"FX3U 软元件 {device} 的用途、范围和使用注意事项是什么？",
            category="device",
            task_type="analysis",
            expected_entities=[device],
        )

    error_rows = connection.execute(
        """
        SELECT error_code,MIN(message),MIN(manual_id)
        FROM error_records
        WHERE error_code_norm <> '0000' AND length(error_code)=4
        GROUP BY error_code_norm
        ORDER BY MIN(pdf_page),error_code
        LIMIT 20
        """
    ).fetchall()
    for index, (code, message, manual_id) in enumerate(error_rows, start=1):
        add(
            case_id=f"error_{index:03d}",
            query=f"FX3U 错误码 {code}：{message}，原因和处理方法是什么？",
            category="error",
            task_type="debug",
            expected_entities=[str(code)],
            expected_chunk_types=["error"],
            expected_manual_ids=[str(manual_id)],
        )

    debug_rows = connection.execute(
        "SELECT case_id,title,symptom,entities_json,task_types "
        "FROM debug_cases ORDER BY case_id"
    ).fetchall()
    for index, (case_id, title, symptom, entities_json, task_types) in enumerate(debug_rows, start=1):
        entities = [str(value) for value in json.loads(str(entities_json))]
        available_tasks = {
            value.strip().casefold()
            for value in str(task_types or "").split(",")
            if value.strip()
        }
        review_task = (
            "program_review"
            if "program_review" in available_tasks
            else "analysis"
            if "analysis" in available_tasks
            else "debug"
        )
        add(
            case_id=f"debug_{index:03d}_a",
            query=f"FX3U 调试：{title}。应检查哪些条件，正确处理方式是什么？",
            category="debug_case",
            task_type="debug",
            expected_entities=entities[:4],
            expected_chunk_types=["debug_case"],
            expected_case_ids=[str(case_id)],
        )
        add(
            case_id=f"debug_{index:03d}_b",
            query=f"程序评审遇到“{symptom}”，请判断是否误报并给出核查依据。",
            category="debug_paraphrase",
            task_type=review_task,
            expected_entities=entities[:4],
            expected_chunk_types=["debug_case"],
            expected_case_ids=[str(case_id)],
        )

    for motion_case in CURATED_MOTION_CASES:
        add(**motion_case)

    structured_rows = connection.execute(
        """
        SELECT c.manual_id,m.manual_number,m.manual_type,c.section,c.chunk_type
        FROM chunks c
        JOIN manuals m ON m.manual_id=c.manual_id
        WHERE c.manual_id NOT IN ('fx3_programming_r','curated_debug_cases')
          AND length(c.section) BETWEEN 8 AND 120
          AND length(c.text) > 500
          AND c.pdf_page > 15
          AND lower(c.section) NOT LIKE '%table of contents%'
          AND lower(c.section) NOT LIKE '%manual number%'
          AND lower(c.section) NOT LIKE '%safety precaution%'
          AND lower(c.section) NOT LIKE '%conditions of use%'
          AND lower(c.section) NOT LIKE '%revision%'
          AND lower(c.section) NOT LIKE '%positioning of this manual%'
          AND lower(c.section) NOT LIKE '%related manuals%'
          AND lower(c.section) NOT LIKE '%warranty%'
        GROUP BY c.manual_id,c.section
        ORDER BY m.priority DESC,c.manual_id,c.pdf_page
        """
    ).fetchall()
    manual_order: list[str] = []
    by_manual: dict[str, list[tuple[str, str, str, str, str]]] = {}
    for manual_id, manual_number, manual_type, section, chunk_type in structured_rows:
        manual_id = str(manual_id)
        if manual_id not in by_manual:
            by_manual[manual_id] = []
            manual_order.append(manual_id)
        by_manual[manual_id].append(
            (
                manual_id,
                str(manual_number),
                str(manual_type),
                str(section),
                str(chunk_type),
            )
        )
    structured_selected = []
    depth = 0
    while len(structured_selected) < 12 and any(
        depth < len(by_manual[manual_id]) for manual_id in manual_order
    ):
        for manual_id in manual_order:
            rows = by_manual[manual_id]
            if depth < len(rows):
                structured_selected.append(rows[depth])
                if len(structured_selected) >= 12:
                    break
        depth += 1
    for index, (manual_id, manual_number, manual_type, section, chunk_type) in enumerate(
        structured_selected, start=1
    ):
        if manual_type == "positioning":
            query = f"FX3U 定位控制手册 {manual_number} 中，{section} 应如何配置和使用？"
            category = "motion_manual_section"
        else:
            query = f"FX3U/GX Works2 {manual_number} 结构化编程中，{section} 应如何理解和使用？"
            category = "structured_programming"
        add(
            case_id=f"structured_{index:03d}",
            query=query,
            category=category,
            task_type="analysis",
            expected_chunk_types=[chunk_type],
            expected_manual_ids=[manual_id],
        )

    for index, query in enumerate(NEGATIVE_QUERIES, start=1):
        add(
            case_id=f"negative_{index:03d}",
            query=query,
            category="negative",
            task_type="edit",
            plc_model="FX5U" if "FX5U" in query.upper() else "FX3U",
            negative=True,
        )

    if len(cases) < target:
        # Deterministically expand high-value instruction and debug cases only.
        originals = [case for case in cases if not case["negative"]]
        round_index = 1
        while len(cases) < target and originals:
            for original in originals:
                if len(cases) >= target:
                    break
                query = f"请结合 FX3U 官方手册复核：{original['query']}（复核场景 {round_index}）"
                add(
                    case_id=f"expanded_{len(cases) + 1:03d}",
                    query=query,
                    category=f"{original['category']}_expanded",
                    task_type=original["task_type"],
                    plc_model=original["plc_model"],
                    expected_entities=list(original["expected_entities"]),
                    expected_chunk_types=list(original["expected_chunk_types"]),
                    expected_manual_ids=list(original["expected_manual_ids"]),
                    expected_case_ids=list(original["expected_case_ids"]),
                )
            round_index += 1
    if len(cases) > target:
        positives = [case for case in cases if not case["negative"]]
        negatives = [case for case in cases if case["negative"]]
        keep_positive = max(0, target - len(negatives))
        cases = positives[:keep_positive] + negatives[: target - keep_positive]
    for sequence, case in enumerate(cases, start=1):
        case["sequence"] = sequence
    return cases


def main() -> int:
    args = parse_args()
    database = args.database.expanduser().resolve()
    output = args.output.expanduser().resolve()
    meta_output = args.meta_output.expanduser().resolve()
    if not database.is_file():
        raise SystemExit(f"database not found: {database}")
    with sqlite3.connect(database) as connection:
        cases = build_cases(connection, int(args.target))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in cases),
        encoding="utf-8",
    )
    categories: dict[str, int] = {}
    for case in cases:
        categories[case["category"]] = categories.get(case["category"], 0) + 1
    meta = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database.name,
        "cases": len(cases),
        "positive_cases": sum(not case["negative"] for case in cases),
        "negative_cases": sum(case["negative"] for case in cases),
        "categories": categories,
        "top_k_for_evaluation": 10,
    }
    meta_output.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(meta, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
