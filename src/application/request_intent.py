"""Request intent."""
import re

_REGENERATE_LOCKED_SPEC_RE = re.compile(
    r"^(?:请)?(?:重新|再次|再|重试)(?:按(?:当前)?已确认规格)?"
    r"(?:生成|尝试生成)(?:程序|方案|一次)?[。！!]*$"
    r"|^(?:please\s+)?(?:regenerate|retry|generate\s+again)(?:\s+the)?(?:\s+(?:program|code|plan))?[.!]*$"
    r"|^(?:プログラムを)?(?:再生成|再試行)(?:してください|して)?[。！!]*$",
    re.IGNORECASE,
)


def _is_regenerate_locked_spec_request(value):
    return bool(_REGENERATE_LOCKED_SPEC_RE.fullmatch(str(value or "").strip()))

