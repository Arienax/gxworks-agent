# Dependency environments

[requirements.txt](../requirements.txt) installs headless development, testing and packaging tools. The component files here are the source of package versions and platform markers; use the same interpreter for installation and execution.

| File | Environment |
| --- | --- |
| [web.txt](web.txt) | Local Web workbench, model provider and packaged MCP launcher |
| [mcp.txt](mcp.txt) | Standalone MCP and its deterministic context/retrieval dependencies |
| [context.txt](context.txt) | Typed context, retrieval components and settings locking |
| [gxw-test.txt](gxw-test.txt) | Independent CFB reader for GXW tests |
| [release.txt](release.txt) | Build both MCP and Full (Web + MCP) Windows packages |
| [release-mcp.txt](release-mcp.txt) | Build only the lean MCP package |
| [development.constraints.txt](development.constraints.txt) | Fixed versions for development and CI, including transitive dependencies and test/build tools |

Development and CI use Python **3.13.14**. Create the development environment
with that interpreter and always use its explicit executable for installation
and checks. On Windows, with `.venv` already created from Python 3.13.14:

```powershell
.venv\Scripts\python.exe -m pip install -c requirements/development.constraints.txt -r requirements.txt
.venv\Scripts\python.exe -m pip check
```

Add `-r requirements/mcp.txt` when working on the independent MCP environment;
browser tests additionally install `playwright==1.55.0` with the same constraints.
Constraints do not install packages on their own and do not pull Windows-only
integrations into Linux environments. Component files retain supported version
ranges for source users; development and CI resolve within the fixed versions.

Routine installation uses this constraint file without `--upgrade`. Dependency
upgrades are separate maintenance: update the intended component requirement and
the affected transitive constraints together, resolve them in the intended
platform environment, and run the affected checks. The pinned set was checked
against the local Windows development environment; CI supplies Linux validation.
Unexecuted domains remain unverified when dependencies change.

Use [getting started](../docs/guides/getting-started.md) for source startup, [MCP](../docs/integrations/mcp.md) for standalone setup and [packaging](../docs/guides/packaging.md) for Windows builds. Optional native integrations require their own installed vendor software.
