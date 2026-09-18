import copy
import json

import application.model_workflows as api
import agent_runtime.agent as plc_agent
from ui.desktop.main_window import SimulatorTestPlanThread, ToolAgentThread
from model_runtime.provider import ReasoningDelta, TextDelta
from plc.ir import build_plc_ir


class _StreamingProvider:
    def __init__(self, events):
        self.events = list(events)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        yield from self.events


def _suite():
    return {
        "schema_version": 1,
        "name": "启动回归",
        "plc_model": "FX3U",
        "tests": [
            {
                "schema_version": 1,
                "name": "启动输出",
                "description": "X0 启动后 Y0 输出",
                "initial": {"X0": 0},
                "steps": [
                    {"at_ms": 5, "set": {"X0": 1}},
                    {"at_ms": 10, "expect": {"Y0": 1}},
                ],
                "trace_devices": ["X0", "Y0"],
                "sample_ms": 5,
                "timeout_ms": 20,
            }
        ],
    }


def _program():
    return build_plc_ir(
        {
            "device_comments": {"X0": "启动", "Y0": "运行"},
            "rungs": [
                {
                    "rung_id": 1,
                    "header_element": None,
                    "branches": [
                        {
                            "branch_id": 1,
                            "y_offset_level": 0,
                            "inputs": [
                                {"type": "NO", "address": "X0", "label": "启动"}
                            ],
                            "outputs": [
                                {"type": "COIL", "address": "Y0", "label": "运行"}
                            ],
                        }
                    ],
                }
            ],
        },
        revision=1,
    )


def test_simulator_test_suite_api_streams_reasoning_and_json(monkeypatch):
    raw = json.dumps(_suite(), ensure_ascii=False)
    provider = _StreamingProvider(
        [
            ReasoningDelta("先识别启动与停止行为。"),
            TextDelta(raw[: len(raw) // 2]),
            TextDelta(raw[len(raw) // 2 :]),
        ]
    )
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    reasoning = []
    content = []
    progress = []

    result = api.generate_simulator_test_suite(
        {"program": "fixture"},
        model_name="fake",
        on_reasoning_chunk=reasoning.append,
        on_content_chunk=content.append,
        on_progress=progress.append,
        raise_errors=True,
    )

    assert result == _suite()
    assert reasoning == ["先识别启动与停止行为。"]
    assert "".join(content) == raw
    assert provider.requests[0].stream is True
    assert provider.requests[0].response_contract.format == "json"
    assert "response_format" not in provider.requests[0].options
    assert progress[0] == "AI 正在生成仿真测试方案（流式）"
    assert progress[-1] == "正在解析模型输出：清理并校验 JSON 结构"


def test_simulator_test_plan_thread_forwards_all_streams(monkeypatch):
    suite = _suite()

    def fake_generate(_context, **kwargs):
        kwargs["on_progress"]("AI 正在生成仿真测试方案（流式）")
        kwargs["on_reasoning_chunk"]("正在选择关键路径。")
        kwargs["on_content_chunk"]('{"schema_version":1}')
        kwargs["on_progress"]("正在解析模型输出：清理并校验 JSON 结构")
        return copy.deepcopy(suite)

    monkeypatch.setattr(
        api,
        "generate_simulator_test_suite",
        fake_generate,
    )

    class Store:
        def __init__(self):
            self.saved = None

        def load_program_ir(self, _project_id, _version_id):
            return _program()

        def save_simulator_test_plan(
            self, _project_id, _version_id, normalized, *, source
        ):
            self.saved = copy.deepcopy(normalized)
            return {"suite": normalized, "source": source}

    store = Store()
    worker = SimulatorTestPlanThread("task-plan", store, "p1", "v1")
    reasoning = []
    content = []
    progress = []
    completed = []
    failed = []
    worker.thinking_updated.connect(
        lambda task_id, token: reasoning.append((task_id, token))
    )
    worker.content_updated.connect(
        lambda task_id, token: content.append((task_id, token))
    )
    worker.progress_updated.connect(
        lambda task_id, message: progress.append((task_id, message))
    )
    worker.completed.connect(
        lambda task_id, result: completed.append((task_id, result))
    )
    worker.failed.connect(lambda task_id, error: failed.append((task_id, error)))

    worker.run()

    assert failed == []
    assert reasoning == [("task-plan", "正在选择关键路径。")]
    assert content == [("task-plan", '{"schema_version":1}')]
    messages = [message for task_id, message in progress if task_id == "task-plan"]
    assert "AI 正在生成仿真测试方案（流式）" in messages
    assert "正在解析模型输出：清理并校验 JSON 结构" in messages
    assert "正在解析模型输出：规范化测试步骤与时间约束" in messages
    assert "正在解析模型输出：保存版本绑定测试方案" in messages
    assert len(completed) == 1
    assert store.saved["tests"][0]["name"] == "启动输出"


def test_tool_agent_thread_forwards_reasoning_content_and_progress(monkeypatch):
    def fake_run(_text, **kwargs):
        kwargs["on_progress"]("AI 正在判断需要使用的工具（第 1 轮）")
        kwargs["on_reasoning_chunk"]("需要读取当前版本。")
        kwargs["on_progress"]("正在执行工具：get_current_program_info")
        kwargs["on_content_chunk"]("当前版本为 v1。")
        return plc_agent.AgentRunResult(
            content="当前版本为 v1。",
            rounds=1,
        )

    monkeypatch.setattr(plc_agent, "run_tool_agent", fake_run)
    worker = ToolAgentThread("task-tool", "当前版本？", context={})
    reasoning = []
    content = []
    progress = []
    completed = []
    failed = []
    worker.thinking_updated.connect(
        lambda task_id, token: reasoning.append((task_id, token))
    )
    worker.content_updated.connect(
        lambda task_id, token: content.append((task_id, token))
    )
    worker.progress_updated.connect(
        lambda task_id, message: progress.append((task_id, message))
    )
    worker.agent_done.connect(
        lambda task_id, payload: completed.append((task_id, payload))
    )
    worker.agent_failed.connect(
        lambda task_id, error: failed.append((task_id, error))
    )

    worker.run()

    assert failed == []
    assert reasoning == [("task-tool", "需要读取当前版本。")]
    assert content == [("task-tool", "当前版本为 v1。")]
    assert [message for _task_id, message in progress] == [
        "AI 正在判断需要使用的工具（第 1 轮）",
        "正在执行工具：get_current_program_info",
    ]
    assert completed[0][1]["content"] == "当前版本为 v1。"
