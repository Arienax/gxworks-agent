# Contributing to GXWorks Agent

Thank you for your interest in contributing to [GXWorks Agent](https://github.com/Arienax/gxworks-agent). We welcome bug reports, documentation improvements, targeted fixes, test cases, and new features.

GXWorks Agent is an extensible engineering toolkit for Mitsubishi PLCs. Its roadmap encompasses **GX Developer, GX Works2, and GX Works3**, with capabilities introduced and verified separately for each product suite, project format, CPU family, and operation. These guidelines do not restrict the project to GX Works2, `.gxw` files, or any single vendor-integration method. Consult the [README](README.md) for currently implemented capabilities rather than treating the roadmap as an immediate support claim.

Engineering-file corruption and incorrect PLC logic carry serious operational and safety consequences. **The contributor submitting a change is strictly accountable for its technical content, safety implications, and verification claims, regardless of whether AI tools were used.**

## Conduct and Ways to Contribute

Keep discussions respectful, constructive, and focused on technical merits.

- **Report bugs:** Describe the affected release, exact reproduction steps, expected versus observed behavior, and relevant error messages. Include the target engineering suite, CPU model, and project format (`.gxw`, `.qgd`, etc.) when applicable.
- **Suggest features or architectural changes:** Open an issue for discussion before investing substantial effort in a major new direction or interface design.
- **Submit code, tests, or documentation:** Keep contributions focused, self-contained, and aligned with existing component ownership and coding conventions.
- **Review pull requests:** Provide concrete, actionable feedback explaining the observed behavior or architectural principle behind each concern.

Do not publicly disclose credentials, customer engineering projects, private session links, or unpatched security details. Contact maintainers privately regarding sensitive issues.

## Getting Started

### Development Environment

- **Python:** Use **Python 3.13** for development. CI and the pinned development environment currently use **3.13.14** for reproducibility; contributors do not need that exact patch release, but older minor releases are not equivalent to the CI environment.
- **Web:** Requires **Node.js 22.14 or newer**, with `npm` and the tracked `web/package-lock.json`.
- **Native C# adapters:** Requires Windows and the **.NET Framework 4.0 C# compiler** at `%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe`. Build scripts invoke `csc.exe` directly, targeting x86 for GX Works2 interoperability. There is no `.csproj`-based native adapter build requiring a modern .NET SDK, so **do not use `dotnet build` or `dotnet test`**. Future product integrations may adopt different toolchains as appropriate.
- **Vendor software:** Mitsubishi software suites are required only for the corresponding native-editor automation, compilation, or simulation checks. Offline file parsing, AST generation, and format research can be contributed without installing vendor products.

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

Install only the dependencies relevant to your contribution scope; see [requirements/README.md](requirements/README.md) for the separate development, Web, and MCP environments.

To compile changes to the existing C# native adapters, run the dedicated build scripts from the repository root:

```powershell
.\tools\build_workspace_adapter.ps1
.\tools\build_active_document.ps1
```

Other native components use specialized scripts, such as `tools/build_hardware_reader.ps1` and `tools/build_simulator_gateway.ps1`. Run only those affected by your changes. Build outputs are placed outside the source tree.

### Language Conventions

- **Discussions & Issues:** Issues, pull request descriptions, and review discussions may use **English or Chinese**.
- **Code & Commits:** Write all commit messages, code identifiers, and ordinary source comments in **English**.
- **Domain Terminology:** Preserve necessary vendor terminology (e.g., Mitsubishi mnemonic names, device designations like `D`, `M`, `ZR`) and localized user-facing text. Follow the style of surrounding code instead of reformatting untouched files.

## Architecture and Engineering Scope

Maintain a strict boundary between product-specific implementations and shared engineering models:

- **Python Core (`src/`):** Owns PLC semantics, CPU architecture, instruction rules, typed engineering ASTs and intermediate representations, file serialization, code generation, and validation. Explicitly preserve product- and CPU-specific distinctions rather than collapsing them into premature generalizations.
- **Web workbench (`web/`):** TypeScript/React client handling user interaction and presentation. It consumes typed contracts and must never become a secondary source of PLC business logic or validation rules.
- **Native adapters (`native_adapters/` and related drivers):** Encapsulate vendor runtime operations behind clean, explicit interfaces. The current C# implementation does not dictate the technology used by future GX Developer or GX Works3 integrations.
- **MCP and other entry points:** Consume shared engineering contracts rather than maintaining inconsistent implementations for different clients.

Do not assume that GX Developer, GX Works2, and GX Works3 share native file formats, object models, language support, automation interfaces, or editing capabilities. Design extensible interfaces that respect each product's native semantics without forcing one tool's model onto another.

Direct, object- and AST-level manipulation of native project files is a foundational architectural pillar of this project, not an incidental GX Works2 constraint. (Note: this refers to operating directly on the internal binary records, object models, and syntax trees of native files across both Simple and Structured project types—including Ladder, ST, FBD, and SFC—rather than the Mitsubishi classification of "Structured Projects".) While UI automation is appropriate for driving live editor sessions, replacing direct file-level manipulation with lossy alternatives (such as CSV/text import or simulated keystrokes) represents a major architectural shift. Any such design change must be discussed and approved in advance.

Avoid speculative compatibility layers, duplicated semantic logic, unrelated refactoring, and committing unvetted generated artifacts.

## AI-Assisted Development

AI coding assistants and autonomous agents are welcome, but **they are held to the exact same architectural, code quality, and review standards as human contributors**. Unreviewed model output, fabricated verification claims, and automated response loops will not be accepted.

1. **Disclose substantive assistance:** In the initial pull request description, identify the AI tools/models used and briefly describe what parts of the contribution they helped produce. Disclose material AI involvement in issue reports when relevant. Never paste private prompts, conversation logs, API credentials, or private environment data.
2. **Review and own the entire diff:** Carefully read and understand generated changes before requesting review. Remove redundant fallback checks, unnecessary abstractions, duplicate logic, incorrect tests, and unrelated edits. Full technical responsibility remains with the contributor.
3. **Understand and explain the solution independently:** You must be able to articulate the technical reasoning, assumptions, and edge cases in your own words. **Do not use maintainer feedback to drive an unverified AI patch loop:** when reviewers raise concerns, investigate the underlying issue, assess the proposed fix yourself, and verify the outcome before pushing updates or replying. AI may aid in debugging or drafting text, but it cannot substitute for human engineering judgment.
4. **Never fabricate validation:** Claim that a test, build, editor operation, compilation, simulation, or hardware check succeeded **only if it was actually performed and the result observed**. Mocks, static analysis, and speculative AI assertions must never be represented as native tool verification.
5. **Keep the contribution reviewable:** Keep diffs focused, provide concise and accurate commit messages, and describe what was physically done rather than copying verbose generated prose. Conduct a thorough self-review before requesting maintainer attention.

Example disclosure snippet for a PR description:

```text
AI assistance: Claude (model: claude-3-7-sonnet) helped implement the parser logic
and draft focused unit tests. I inspected and edited the diff, removed unnecessary fallbacks,
and ran the offline test suite. Native editor verification was not performed.
```

Maintainers reserve the right to request clarification, defer review, or decline pull requests where the author cannot explain the underlying code, repeatedly submits unreviewed machine output, or misrepresents verification.

## Development Workflow

Create a focused branch from `main` using descriptive naming (e.g., `feat/gxworks3-import`, `fix/native-project-selection`, `docs/contributing`). Prefer one coherent change per PR; separate independent refactorings or orthogonal features into separate PRs to keep reviews manageable.

Use English [Conventional Commits](https://www.conventionalcommits.org/) for commit messages and PR titles:

```text
feat(project): add support for a native project format
fix(native): preserve explicit project selection
refactor(core): share CPU instruction resolution
docs: clarify multi-product integration
```

### Testing and Verification

**Validate changes within the affected subsystem, and report only the checks that were genuinely performed.** Add focused automated tests for behavior changes where practical. (Documentation changes do not require executing code test suites.)

Useful commands, run from the repository root unless noted:

```powershell
# Python syntax and repository-specific boundary checks
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\python.exe tools/check_public_boundary.py

# Targeted Python tests: replace with the affected test file
.venv\Scripts\python.exe -m pytest -q tests/test_source_layout.py

# Web tests and TypeScript checking/build (run inside web/)
cd web
npm test
npm run build
cd ..
```

> [!NOTE]
> `npm run build` executes `tsc -b` before invoking Vite. The repository does not currently enforce a general `npm run lint`, Ruff, or mypy workflow; do not claim to have run tools that are not part of the active pipeline. For C# native adapter changes, compile the affected adapter using the scripts under **Getting Started**, followed by relevant Python integration tests. Successful compilation does not prove runtime compatibility inside the vendor editor.

Refer to [tests/README.md](tests/README.md) to locate the appropriate test owner before introducing new tests. Prefer minimal, synthetic, code-constructed fixtures or concise byte sequences over raw project files. Do not commit multi-megabyte binary projects, disassembly dumps, or bulk reverse-engineering traces to test a localized feature. If a native binary sample is indispensable, justify its inclusion, reduce it to a minimal reproduction, and ensure it is legally distributable.

Contributions to the public repository must also pass `python tools/check_public_boundary.py`. Follow the shared exclusions in [publication/source_policy.py](publication/source_policy.py) and the [public source separation guide](publication/README.md); ordinary source files do not require registration in a per-file inventory.

Distinguish the following verification levels when describing results:

| Checked Operation | What It Establishes |
| :--- | :--- |
| **Static inspection / Mocked tests** | Validates internal syntax, code structure, or mock-bound control flow only. |
| **Offline parse, edit, and save** | Confirms internal data integrity and serialization, but does **not** prove vendor editor acceptance. |
| **Open / Save in native editor** | Proves vendor editor acceptance of the project file structure, but does **not** confirm compilation or logic validity. |
| **Native compilation / Diagnostics** | Confirms syntax and semantic acceptance for the specific product version, CPU type, and program under test. |
| **Simulation / Hardware execution** | Verifies logic behavior under the specific test scenario; does **not** guarantee general operational safety on real machinery. |

When reporting native verification, explicitly specify the **exact product name and version**, CPU model, project type, operation performed, and observed result. If native software or PLC hardware is inaccessible, state **"Not tested"** plainly—contributions with transparently stated limitations are far more valuable than unverified claims.

## Pull Request Process

1. **Explain context:** Outline the problem being addressed, the proposed approach, and link any related issues.
2. **Summarize diff:** Provide a clear summary of changes, highlighting any breaking changes, schema modifications, or behavioral shifts.
3. **Complete PR template:** Fill out all applicable fields in [.github/pull_request_template.md](.github/pull_request_template.md), including executed verification and known limits.
4. **Disclose AI assistance:** State any substantive use of AI assistants as outlined in the AI policy above.
5. **Update documentation:** Keep user-facing docs, API signatures, and type declarations synchronized with code changes.
6. **Address review comments:** Respond thoughtfully to reviewer feedback. If an environment constraint prevents you from verifying a requested change, state this transparently rather than guessing.

Do not describe an implementation as complete simply because an AI assistant produced code or a CI check passed. Maintainers may request revisions, defer review, or decline PRs that do not meet architectural standards, safety expectations, or review capacity.

## Security, Licensing, and Safety

All contributions must comply with [Apache License 2.0](LICENSE), [LICENSE-SCOPE.md](LICENSE-SCOPE.md), [NOTICE](NOTICE), and applicable [third-party notices](THIRD_PARTY_NOTICES.md). Submit only materials you have the legal right to contribute. Do not commit proprietary vendor manuals, unauthorized vendor binaries, confidential customer PLC projects, secrets, or private links. Use minimal sanitized reproductions when reporting defects.

GXWorks Agent does not replace the engineering review, safeguards, and commissioning procedures required for industrial machinery. Changes involving PLC outputs, interlocks, motion, or equipment control must not be represented as safe for deployment solely because they passed software-level checks.
