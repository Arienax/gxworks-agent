"""SFC requirement semantics, independent of a particular canvas."""
from __future__ import annotations


def graph_requirement(blocks, conns, io_config=None, *, translate):
    """将 SFC 流程图转换为结构化文本描述"""
    tr = translate
    by_id = {b["id"]: b for b in blocks}
    if not blocks: return ""

    def next_blocks(blk):
        return [by_id[c["target"]] for c in conns if c["source"] == blk["id"]]
    def prev_blocks(blk):
        return [by_id[c["source"]] for c in conns if c["target"] == blk["id"]]
    def label_of(blk):
        return blk["label"].strip() or tr('（未命名）')

    # 找入口
    roots = [b for b in blocks if not prev_blocks(b)]
    if not roots: roots = blocks
    roots.sort(key=lambda b: b["y"])

    lines = []
    if io_config:
        for key, lbl in [("inputs","X"),("outputs","Y"),("registers","D/T")]:
            for addr,desc in io_config.get(key,{}).items():
                lines.append(f"  {lbl}{addr}" + (f"（{desc}）" if desc and desc!=addr else ""))
        if lines: lines.insert(0,tr('【硬件映射】')); lines.append("")

    visited, step_num = set(), [0]
    lines.append(tr('【步进控制逻辑】')); lines.append("")

    def traverse(start, indent=""):
        queue = [start]
        while queue:
            blk = queue.pop(0)
            if blk["id"] in visited: continue
            visited.add(blk["id"])
            label = label_of(blk)
            if blk["type"] == "step":
                step_num[0] += 1
                lines.append(f"{indent}S{step_num[0]}：{label}")
                nxt = next_blocks(blk)
                if len(nxt) > 1:
                    lines.append(tr('{v0}  [并行分支]', v0=indent))
                    for i,n in enumerate(nxt):
                        lines.append(tr('{v0}  子路径{v1}：', v0=indent, v1=i + 1))
                        sub_q = [n]; sub_d = 0
                        while sub_q and sub_d < 10:
                            cur = sub_q.pop(0)
                            if cur["id"] in visited: continue
                            visited.add(cur["id"])
                            lbl = label_of(cur)
                            if cur["type"] == "transition":
                                lines.append(f"{indent}    ├ {lbl}")
                            else:
                                step_num[0] += 1
                                lines.append(f"{indent}    S{step_num[0]}：{lbl}")
                            for nn in next_blocks(cur):
                                if nn["id"] not in visited: sub_q.append(nn)
                            sub_d += 1
                    lines.append(tr('{v0}  [汇合]', v0=indent))
                elif len(nxt) == 1:
                    queue.append(nxt[0])
            else:
                lines.append(tr('{v0}  → 条件：{v1}', v0=indent, v1=label))
                for n in next_blocks(blk):
                    if n["id"] not in visited: queue.append(n)

    for root in roots:
        if root["id"] not in visited: traverse(root)

    lines.append("")
    lines.append(tr('请根据以上步进控制逻辑生成完整的梯形图 JSON。'))
    return "\n".join(lines)


def next_io_address(prefix, saved_addresses, draft_addresses):
    """Retain the legacy editor's allocation behavior without a UI dependency."""
    numbers = [int(address[len(prefix):]) for address in saved_addresses
               if address.startswith(prefix) and address[len(prefix):].isdigit()]
    for raw in draft_addresses:
        address = raw.strip()
        if address.startswith(prefix) and address[len(prefix):].isdigit():
            numbers.append(int(address[len(prefix):]))
    return f"{prefix}{max([-1] + numbers) + 1}"


def linear_requirement(steps):
    return "\n".join(f"步骤 {i + 1}：{s['name']}\n动作：{s['action']}\n转移条件：{s.get('transition') or '流程结束'}" for i, s in enumerate(steps))


def flowchart_model(steps: list):
    """
    根据 AI 分析结果自动填充流程图，支持分支结构。
    steps: [
      {"type":"step","label":"初始化"},
      {"type":"transition","label":"X0启动"},
      {"type":"fork","label":"分两路"},           # 并行分支开始
      {"type":"step","label":"Y0运行","branch":0},
      {"type":"transition","label":"T0到","branch":0},
      {"type":"step","label":"Y1运行","branch":1},
      {"type":"transition","label":"T1到","branch":1},
      {"type":"join","label":"汇合"},              # 并行分支结束
      {"type":"step","label":"完成"},
    ]
    无 fork/join 时退化为单线流程。
    """
    nodes, edges = [], []
    if not steps:
        return {"nodes": nodes, "edges": edges}
    COL_GAP = 200      # 分支列间距
    ROW_GAP = 90       # 块行间距
    CENTER_X = 200     # 主干 x 坐标

    def make_block(bt, label, x, y):
        node = {"id": len(nodes), "type": "step" if bt in ("step", "fork", "join") else "transition",
                "label": label, "x": x, "y": y}
        nodes.append(node)
        return node

    def connection(source, target):
        return {"source": source["id"], "target": target["id"]}

    # ---- 解析分支 ----
    # 找出所有 fork/join 位置
    forks = {}   # index -> branch_count
    joins = set()
    max_branch = 0
    for i, s in enumerate(steps):
        if s.get("type") == "fork":
            forks[i] = 0
        elif s.get("type") == "join":
            joins.add(i)
        br = s.get("branch", -1)
        if isinstance(br, int) and br > max_branch:
            max_branch = br
    # 推算每个 fork 的分支数
    fork_indices = list(forks.keys())
    for fi, f_idx in enumerate(fork_indices):
        next_join = None
        for j in joins:
            if j > f_idx:
                next_join = j
                break
        if next_join:
            branches_in_range = set()
            for k in range(f_idx + 1, next_join):
                br = steps[k].get("branch", -1)
                if isinstance(br, int) and br >= 0:
                    branches_in_range.add(br)
            forks[f_idx] = max(branches_in_range) + 1 if branches_in_range else 2
        else:
            forks[f_idx] = max_branch + 1 if max_branch >= 0 else 2

    # ---- 布局 ----
    blocks = []          # 按 steps 顺序存放 (block, x, y)
    y = 20
    x = CENTER_X
    i = 0
    while i < len(steps):
        s = steps[i]
        bt = s.get("type", "step")
        label = s.get("label", "")

        if bt == "fork":
            b = make_block("step", label, x, y)
            blocks.append((b, x, y))
            fork_idx = i
            branch_count = forks.get(i, 2)
            y += ROW_GAP
            i += 1
            # 处理各分支
            branch_rows = [y] * branch_count
            branch_x = [x - (branch_count - 1) * COL_GAP / 2 + b * COL_GAP for b in range(branch_count)]
            branch_done = [False] * branch_count
            while i < len(steps):
                s2 = steps[i]
                if s2.get("type") == "join":
                    break
                br = s2.get("branch", None)
                if isinstance(br, int) and 0 <= br < branch_count:
                    b2 = make_block(s2.get("type", "step"), s2.get("label", ""), branch_x[br], branch_rows[br])
                    blocks.append((b2, branch_x[br], branch_rows[br]))
                    branch_rows[br] += ROW_GAP
                i += 1
            # join 块放在分支最大 y 处
            y = max(branch_rows)
            continue

        elif bt == "join":
            b = make_block("step", label, x, y)
            blocks.append((b, x, y))
            y += ROW_GAP
            i += 1

        else:
            b = make_block(bt, label, x, y)
            blocks.append((b, x, y))
            y += ROW_GAP
            i += 1

    # ---- 连线 ----
    for idx in range(len(blocks) - 1):
        b1, x1, y1 = blocks[idx]
        b2, x2, y2 = blocks[idx + 1]
        # 跳过 fork→第一分支 和 末分支→join 的连接（跨列由分支处理）
        s1_type = steps[idx].get("type", "") if idx < len(steps) else ""
        s2_type = steps[idx + 1].get("type", "") if idx + 1 < len(steps) else ""
        # fork 块 → 各分支第一块
        if s1_type == "fork":
            fork_x, fork_y = x1, y1
            branch_count = forks.get(idx, 2)
            for br in range(branch_count):
                # 找第一个 branch==br 的块
                for k in range(idx + 1, len(steps)):
                    if steps[k].get("type") == "join":
                        break
                    kb = steps[k].get("branch", -1)
                    if isinstance(kb, int) and kb == br:
                        _, bx, by = blocks[k]  # 注意: blocks 和 steps 索引对齐
                        conn = connection(b1, blocks[k][0])
                        edges.append(conn)
                        break
            continue
        # 各分支末块 → join
        if s2_type == "join":
            join_x, join_y = x2, y2
            for k in range(idx, -1, -1):
                if steps[k].get("type") == "fork":
                    break
                if steps[k].get("type") not in ("join",):
                    conn = connection(blocks[k][0], b2)
                    edges.append(conn)
            # 只连分支末块——这里简化处理，每个 branch 末块都连
            continue
        # 普通顺序连接
        if s1_type != "fork" and s2_type != "join":
            conn = connection(b1, b2)
            edges.append(conn)

    return {"nodes": nodes, "edges": edges}


def document_graph(document):
    """Project a saved version-1 .sfc document into the existing graph input.

    Preserve properties, geometry and unknown document fields in the source:
    this operation only creates a detached projection and never edits a file.
    This is a requirements format, not a native GX Works2 SFC program.
    """
    from copy import deepcopy
    if not isinstance(document, dict) or document.get("version", 1) != 1:
        raise ValueError("Unsupported SFC document version")
    nodes = []
    for block in document.get("blocks", []):
        node = deepcopy(block)
        node["id"] = block["temp_id"]
        nodes.append(node)
    edges = [{"source": edge["source_id"], "target": edge["target_id"]}
             for edge in document.get("connections", [])]
    ids = [node["id"] for node in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate SFC block identity")
    if any(edge[side] not in ids for edge in edges for side in ("source", "target")):
        raise ValueError("SFC connection refers to a missing block")
    return {"nodes": nodes, "edges": edges,
            "io_config": deepcopy(document.get("io_config", {}))}


def document_requirement(document, *, translate):
    """Convert an existing .sfc document using the unchanged graph semantics."""
    graph = document_graph(document)
    return graph_requirement(graph["nodes"], graph["edges"], graph["io_config"], translate=translate)


def main(argv=None):
    """Read-only source utility: python -m plc.sfc control_flow.sfc."""
    import argparse
    import json
    from pathlib import Path
    from shared.i18n import tr
    parser = argparse.ArgumentParser(description="Convert saved SFC requirements to text; no GX/PLC operations")
    parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.source.read_text(encoding="utf-8-sig"))
        text = document_requirement(document, translate=tr)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
