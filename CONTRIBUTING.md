# Contributing to GXWorks Agent

Thank you for contributing to [GXWorks Agent](https://github.com/Arienax/gxworks-agent). Bug reports, documentation, tests, focused fixes, and new features are welcome.

GXWorks Agent is an extensible toolkit for Mitsubishi PLC engineering. Its roadmap includes **GX Developer, GX Works2, and GX Works3**, with capabilities introduced separately for each product, project format, CPU family, and operation. These guidelines do not restrict the project to GX Works2, `.gxw` files, or any single vendor-integration method. Consult the [README](README.md) for currently implemented features rather than treating the roadmap as a support claim.

Engineering-file corruption and incorrect PLC logic can have serious consequences. **The contributor submitting a change is accountable for its technical content and the accuracy of its claims, regardless of AI assistance.**

## Conduct and Ways to Contribute

Keep discussions respectful, constructive, and focused on technical issues.

- **Report a bug:** Describe the affected release, reproduction steps, expected and actual behavior, and relevant error messages. Include the engineering product, CPU, and project format when applicable.
- **Suggest a feature or architectural change:** Open an issue for discussion before investing in a substantial new direction or interface.
- **Submit code, tests, or documentation:** Keep the contribution focused and follow existing ownership and style conventions.
- **Review a PR:** Explain concrete concerns and the behavior or design principle behind them.

Do not publicly disclose credentials, customer engineering projects, private session links, or unpatched security details. Contact maintainers privately about sensitive issues.

## Getting Started

### Development environment

- **Python:** Use **Python 3.13** for development. CI and the pinned development environment currently use **3.13.14**; that is a reproducibility choice, not a requirement that every contributor install exactly that patch release. Other versions may work for particular entry points but are not equivalent to the CI environment.
- **Web:** Node.js **22.14 or newer**, with npm and the checked-in `web/package-lock.json`.
- **Native C# adapters:** Windows and the **.NET Framework C# compiler** at `%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe`. The current scripts compile the adapters directly and use x86 for the GX Works2-facing executables. There is currently no `.csproj`-based native adapter build requiring a .NET SDK, so **`dotnet build` and `dotnet test` are not the applicable commands**. Future product integrations may use different toolchains.
- **Vendor software:** Required only for the corresponding native-editor, compilation, or simulation operations. Offline code and file-format work can be contributed without installing every Mitsubishi product.

From Windows PowerShell:

```powershell
git clone https://github.com/<your-username>/gxworks-agent.git
cd gxworks-agent
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -c requirements/development.constraints.txt -r requirements.txt

cd web
npm ci
cd ..
```

Install only the dependencies relevant to your contribution; see [requirements/README.md](requirements/README.md) for the separate development, Web, and MCP environments.

For changes to the existing C# native adapters, use the actual build scripts from the repository root:

```powershell
.\tools\build_workspace_adapter.ps1
.\tools\build_active_document.ps1
```

Other native components have their own scripts, such as `tools/build_hardware_reader.ps1` and `tools/build_simulator_gateway.ps1`. Run only those affected by your change. The scripts place their outputs outside the source repository.

### Language conventions

Issues, PR descriptions, and review discussions may use **English or Chinese**. Write commit messages, new code identifiers, and ordinary source comments in **English**. Preserve necessary vendor terminology, PLC device names, and localized user-facing text. Follow the style of the surrounding code instead of reformatting unrelated files.

Coding agents must follow the same architectural and review requirements as human contributors.

## Architecture and Engineering Scope

Keep product-specific behavior separate from shared engineering concepts:

- **Python Core (`src/`):** Owns PLC semantics, CPU and instruction rules, structured engineering representations, file-format logic, generation, and validation. Preserve product- and CPU-specific distinctions explicitly.
- **Web workbench (`web/`):** TypeScript/React handles presentation and interaction; it must not become a second source of PLC semantic rules.
- **Native adapters (`native_adapters/` and related integrations):** Implement editor- and vendor-facing operations behind clear interfaces. The current C# implementation does not dictate the technology used by future GX Developer or GX Works3 integrations.
- **MCP and other entry points:** Reuse shared engineering contracts rather than maintaining inconsistent implementations for different clients.

Do not assume that GX Developer, GX Works2, and GX Works3 share native file formats, object models, language support, automation interfaces, or editing capabilities. Design extensible interfaces without forcing a product to emulate another product's implementation.

Structured editing of native engineering projects is an important project direction, **not a GXW-only design constraint**. UI automation may be appropriate for editor operations, but replacing structured native-file editing with CSV import or UI automation is a substantive design change that should be discussed with maintainers, not introduced as an unannounced substitute.

Avoid unrelated refactors, duplicated semantic logic, speculative compatibility layers, and unnecessary generated outputs.

## AI-Assisted Development

AI coding assistants are welcome. **Unreviewed agent output and misleading descriptions are not.** These five requirements apply to AI-generated code, tests, documentation, and review responses:

1. **Disclose substantive assistance.** In the initial PR description, name the AI tools/models used and briefly describe what they helped produce. Disclose material AI involvement in an issue report when relevant. Never attach private prompts, conversation logs, API credentials, or unrelated account information.
2. **Review and own the entire diff.** Read and understand generated changes before requesting review. Remove redundant fallbacks, unnecessary abstractions, duplicated logic, incorrect tests, and unrelated edits. Responsibility remains with the contributor.
3. **Explain and investigate independently.** Be able to explain the implementation, its assumptions, and the reasons for a fix in your own words. **Do not turn maintainer comments into an automatic AI patch loop:** investigate the defect, assess the proposed correction, and review the result before pushing or replying. AI may help with analysis or wording, but not replace your technical judgment.
4. **Never fabricate validation.** Claim a test, build, native-editor action, compile, simulation, or hardware check passed **only if it was actually performed and the result observed**. Static analysis, mocks, and an AI prediction must not be represented as native-tool execution.
5. **Keep the contribution reviewable.** Use meaningful commits, keep unrelated changes out of scope, and describe the actual work rather than copying long generated explanations. Complete your own review before requesting a maintainer's review.

For example, a PR can include:

```text
AI assistance: Codex (model: <model name>) helped implement the parser change
and draft focused tests. I inspected the full diff and revised the result.
The validation section distinguishes executed checks from untested behavior.
```

No private AI conversation history is required. Maintainers may request clarification or close a PR if the author cannot explain its implementation, repeatedly submits unreviewed changes, or misrepresents verification.

## Development Workflow

Create a focused branch from `main`, such as `feat/gxworks3-import`, `fix/native-project-selection`, or `docs/contributing`. Prefer one coherent change per PR; separate independent work when it materially improves reviewability.

Use English [Conventional Commits](https://www.conventionalcommits.org/) for commit messages and PR titles:

```text
feat(project): add support for a native project format
fix(native): preserve explicit project selection
refactor(core): share CPU instruction resolution
docs: clarify multi-product integration
```

### Testing and Verification

**Validate the affected behavior, and report only the scope actually checked.** Add focused tests for behavior changes where practical. Documentation-only changes need relevant documentation checks, not unrelated code tests.

Useful existing commands, run from the repository root unless noted:

```powershell
# Python syntax and repository-specific boundary checks
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\python.exe tools/check_public_boundary.py

# Targeted Python tests: replace with the affected existing test file
.venv\Scripts\python.exe -m pytest -q tests/test_source_layout.py

# Web tests and TypeScript checking/build (run inside web/)
cd web
npm test
npm run build
cd ..
```

`npm run build` executes `tsc -b` before Vite. The repository does not currently provide a general `npm run lint`, Ruff, or mypy workflow; do not claim to have run one. For native C# changes, compile the affected adapter using the scripts listed under **Getting Started**, then run the relevant Python integration tests if applicable. Compilation alone does not establish successful operation inside an editor.

Use [tests/README.md](tests/README.md) to find the existing test owner before adding new tests. Prefer focused, code-constructed examples and minimal byte sequences for engineering-file tests. Do not add large binary projects, raw reverse-engineering archives, or generated evidence packages merely to demonstrate a small behavior. If a native binary sample is genuinely necessary, explain why it is required, keep it minimal, and ensure you have permission to distribute it.

Distinguish the following when describing results:

| Checked operation | What it establishes |
| --- | --- |
| Static inspection or mocked execution | Only the inspected logic or simulated interface behavior |
| Offline parse, edit, and save | The tested file operation, not acceptance by a vendor editor |
| Open/save in a native editor | Acceptance for the tested editor and project, not compilation |
| Native compilation or editor diagnostics | The observed result for the tested product, version, CPU, and source |
| Simulation or hardware execution | Only the observed scenario and environment; not general machine safety |

For native checks, identify the **actual product and version**, CPU/project type, operation, and observed outcome. If software or hardware is unavailable, say **not tested** and explain the limitation. Contributors do not need every Mitsubishi product installed to make a valuable contribution.

## Pull Request Process

1. Explain the problem, proposed change, and relevant issue (if any).
2. Summarize the real diff and any compatibility or behavior changes.
3. Fill in the existing [PR template](.github/pull_request_template.md), including the applicable verification results and limitations.
4. Disclose substantive AI assistance as described above.
5. Keep documentation current when user-visible behavior or interfaces change.
6. Address review comments substantively. If a concern cannot be resolved or checked in your environment, say so rather than asserting completion.

Do not describe an implementation as complete simply because an agent produced code or a CI job passed. A maintainer may request changes, defer review, or decline a PR based on architecture, quality, scope, licensing, or review capacity.

## Security, Licensing, and Safety

Contributions must comply with [Apache License 2.0](LICENSE), [LICENSE-SCOPE.md](LICENSE-SCOPE.md), [NOTICE](NOTICE), and applicable [third-party notices](THIRD_PARTY_NOTICES.md). Submit only materials you have the right to contribute. Do not commit proprietary vendor manuals, unauthorized vendor binaries, confidential customer PLC projects, secrets, or private links. Use small sanitized reproductions when reporting defects.

GXWorks Agent does not replace the engineering review, safeguards, and commissioning procedures required for industrial machinery. Changes involving PLC outputs, interlocks, motion, or equipment control must not be represented as safe for deployment solely because they passed software-level checks.
