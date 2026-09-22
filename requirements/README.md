# Dependency environments

[requirements.txt](../requirements.txt) installs headless development, testing and packaging tools. The component files here are the source of package versions and platform markers; use the same interpreter for installation and execution.

| File | Environment |
| --- | --- |
| [web.txt](web.txt) | Local Web workbench, model provider and packaged MCP launcher |
| [mcp.txt](mcp.txt) | Standalone MCP and its deterministic context/retrieval dependencies |
| [context.txt](context.txt) | Typed context, retrieval components and settings locking |
| [gxw-test.txt](gxw-test.txt) | Independent CFB reader for GXW tests |

Use [getting started](../docs/guides/getting-started.md) for source startup, [MCP](../docs/integrations/mcp.md) for standalone setup and [packaging](../docs/guides/packaging.md) for Windows builds. Optional native integrations require their own installed vendor software.
