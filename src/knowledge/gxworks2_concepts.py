"""Shared, task-scoped routes for GX Works2 supporting knowledge.

Weak software words are never indexed as bare derived entities. Their
namespaced routes require explicit PLC/ST context and a matching topic.
"""

from __future__ import annotations

import re
import unicodedata


CONTEXT_RE = re.compile(
    r"gx\s*works\s*[23]?|structured\s*text|(?<![A-Za-z0-9_])ST(?![A-Za-z0-9_])|"
    r"FX3(?:S|GC|G|UC|U)|mitsubishi|三菱|软元件|梯形图|\bPLC\b", re.IGNORECASE,
)

TASK_SCOPE = {
    "st_rule": "st,generate,edit",
    "data_type": "st,generate,edit,analysis",
    "compatibility": "st,generate,analysis",
}

CONCEPT_ROUTES = {
    "st_rule": {
        "CONTINUE": ("continue",),
        "VAR_IN_OUT": ("var_in_out",),
        "TON": ("assignment operator", "tondelay", "ton("),
        "ARRAY": ("array[*]", "variable-length arrays"),
        "__NEW": ("__new", "dynamic memory"),
        "__DELETE": ("__delete", "dynamic memory"),
        "POU": ("pou", "fb/fun/prg", "program pou"),
        "PRG_INIT": ("prg_init",),
        "PRG_MAIN": ("prg_main",),
        "PRG_PROCESS": ("prg_process",),
        "FB_MOTOR": ("fb_motor",),
        "FBMOTOR": ("fbmotor",),
        "GXW2_COMMENT_STYLE": ("## comment style",),
        "GXW2_CASE_LABELS": ("### case statement",),
        "GXW2_FB_OUTPUT": ("assignment operator", "fb outputs"),
        "GXW2_SR_RS": ("bistable", "`sr`, `rs`"),
        "GXW2_POU_NAMING": ("## naming conventions", "pou and file naming"),
        "GXW2_PROGRAM_STRUCTURE": ("3-program structure",),
        "GXW2_DYNAMIC_MEMORY": ("dynamic memory", "__new", "__delete"),
    },
    "data_type": {
        "LREAL": ("lreal",),
        "WSTRING": ("wstring",),
        "LTIME": ("ltime",),
        "REF_TO": ("ref_to",),
        "DINT": ("memory consumption", "d registers consumed"),
        "DWORD": ("memory consumption", "d registers consumed"),
        "K100": ("mitsubishi literal notation", "literal examples"),
        "HFF": ("mitsubishi literal notation", "literal examples"),
        "E3": ("mitsubishi literal notation", "literal examples"),
        "INT_TO_REAL_E": ("int_to_real_e", "_e postfix pattern"),
        "GXW2_REAL_MEMORY": ("memory consumption", "d registers consumed"),
        "GXW2_TIME_TYPE": ("elementary types", "time conversions"),
    },
    "compatibility": {
        "GXW2_STRING_SUPPORT": ("feature matrix",),
        "FX3S": ("device ranges", "fx3s"),
        "WORKS3": ("gx works 2 vs gx works 3", "gx works 3"),
    },
}

SKILL_CONCEPTS = frozenset(concept for routes in CONCEPT_ROUTES.values() for concept in routes)
STRONG_CONCEPTS = frozenset(concept for concept in SKILL_CONCEPTS if not concept.startswith("GXW2_"))

# Migration vocabulary includes every token injected by the old v1/v2 tuner,
# even tokens which no longer have a route. Native importer entities are kept.
LEGACY_DERIVED_CONCEPTS = frozenset("""
CONTINUE VAR_IN_OUT CASE RANGE LABEL TON OUTPUT SR RS ARRAY NEW DELETE DYNAMIC
MEMORY FB FUN PROGRAM POU INSTANCE PRG_INIT PRG_MAIN PRG_PROCESS FB_MOTOR FBMOTOR
COMMENT COMMENTS STRUCTURED TEXT LREAL WSTRING LTIME REF_TO DINT DWORD REAL
STRING TIME K100 HFF E3 INT_TO_REAL_E FX3S WORKS3
""".split())


def query_skill_concepts(query, task_type="generate"):
    normalized = unicodedata.normalize("NFKC", str(query or ""))
    terms = {match.group(0).upper() for match in re.finditer(
        r"(?<![A-Za-z0-9_])(?:__[A-Za-z]+|[A-Za-z][A-Za-z0-9_]*)(?![A-Za-z0-9_])", normalized,
    )}
    concepts = terms.intersection(STRONG_CONCEPTS)
    if CONTEXT_RE.search(normalized):
        def has(pattern):
            return bool(re.search(pattern, normalized, re.IGNORECASE))

        if has(r"\bcomments?\b|注释|//|\(\*"):
            concepts.add("GXW2_COMMENT_STYLE")
        if "CASE" in terms or has(r"范围标签|命名状态标签"):
            concepts.add("GXW2_CASE_LABELS")
        if has(r"\boutputs?\b|输出参数"):
            concepts.add("GXW2_FB_OUTPUT")
        if terms.intersection({"SR", "RS"}) and (
            {"SR", "RS"}.issubset(terms) or has(r"\bFBs?\b|bistable|双稳态|功能块")
        ):
            concepts.add("GXW2_SR_RS")
        naming_topic = terms.intersection({"FB", "FUN", "POU", "PROGRAM", "FB_MOTOR", "FBMOTOR"}) or has(
            r"function\s+blocks?|variables?|instances?|变量|实例",
        )
        if naming_topic and has(r"naming|file.?names?|instance.?names?|命名|文件名|实例名"):
            concepts.add("GXW2_POU_NAMING")
        if has(r"program\s+(?:structure|layout)|程序结构|项目结构|三个程序"):
            concepts.add("GXW2_PROGRAM_STRUCTURE")
        if has(r"dynamic\s+memory|动态内存"):
            concepts.add("GXW2_DYNAMIC_MEMORY")
        if "REAL" in terms and has(r"memory|register|内存|寄存器"):
            concepts.add("GXW2_REAL_MEMORY")
        if "TIME" in terms:
            concepts.add("GXW2_TIME_TYPE")
        if "STRING" in terms or "字符串" in normalized:
            concepts.add("GXW2_STRING_SUPPORT")
        if has(r"gx\s*works\s*3"):
            concepts.add("WORKS3")
    task = str(task_type or "generate").casefold()
    allowed = {concept for role, routes in CONCEPT_ROUTES.items()
               if task in TASK_SCOPE[role].split(",") for concept in routes}
    return sorted(concepts.intersection(allowed))
