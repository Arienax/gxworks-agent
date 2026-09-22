# Source layout

## Package navigation

| Package | Responsibility |
| --- | --- |
| [application](../../src/application/) | Use cases, jobs, approvals, projects and model workflows |
| [model_runtime](../../src/model_runtime/) | Provider transport, profiles, capability contracts and observations |
| [knowledge](../../src/knowledge/) | Scoped retrieval, indexes, evidence and ranking |
| [plc](../../src/plc/) | PLC IR, specifications, instructions, deterministic validation and artifacts |
| [gxw](../../src/gxw/) | GXW containers, objects, declarations and preserved native records |
| [gxworks2](../../src/gxworks2/) | GX Works2 CSV and native integration |
| [simulator](../../src/simulator/) | Test definitions, execution and verification |
| [agent_runtime](../../src/agent_runtime/) | Shared tool messages, registry and agent orchestration |
| [rendering](../../src/rendering/) | Headless SVG rendering |
| [inspection](../../src/inspection/) | Inspection engine and view models |
| [storage](../../src/storage/) | Configuration, credentials and project persistence |
| [shared](../../src/shared/) | Resource paths, language, diagnostics and tracing |
| [integrations](../../src/integrations/) | Web and MCP protocol adapters |

[Language boundaries](language-boundaries.md) define which layer may interpret PLC semantics. Put new modules in their owning package. The root [api.py](../../src/api.py) is the explicit compatibility facade; application internals use [application.model_api](../../src/application/model_api.py).

## Entry points

Source imports use `PYTHONPATH=src`. The Web launcher is [integrations.web.__main__.main](../../src/integrations/web/__main__.py); MCP uses [integrations.mcp.__main__.main](../../src/integrations/mcp/__main__.py). Installation and commands belong to the [getting-started guide](../guides/getting-started.md) and [MCP reference](../integrations/mcp.md).

The headless utilities [gxw.decoder](../../src/gxw/decoder.py) and [plc.sfc](../../src/plc/sfc.py) read GXW/POU data and convert legacy SFC input to requirements. Inspect each module's `--help` before supplying input files.

## Resources and state

Bundled resources are separate from writable user settings and project workspaces. Configuration lookup and migration belong to [storage.config](../../src/storage/config.py); query and backup instructions are in [model settings](../guides/model-settings.md#storage).

Historical prompt implementations under `research/baselines` support comparisons only. [tools/source_layout.json](../../tools/source_layout.json) records old-to-new module paths; it is a navigation map, not an import hook. Migration experiments and test results are in the [process index](../process/README.md).

## Verification

[Test ownership](../../tests/README.md) defines the test boundaries. Source layout and architecture checks live in [test_source_layout.py](../../tests/test_source_layout.py) and [test_architecture_boundaries.py](../../tests/test_architecture_boundaries.py). Packaging and generated-type checks use the existing scripts described in [packaging](../guides/packaging.md) and [HTTP reference](../integrations/http-api.md).
