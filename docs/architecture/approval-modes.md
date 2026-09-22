# Workbench approvals

## Local saving and external actions

A directly requested Web generation, edit, GXW import, FBD conversion or GX read can save a validated local version through the existing transaction. The transaction checks the candidate, specification, base version and artifacts. Invalid candidates and conflicting proposals do not become active programs.

External actions use the workspace's approval policy:

| Mode | Execution consent |
| --- | --- |
| `ask` | Review GX import, simulation and debug actions individually |
| `auto` | Delegate eligible version-bound simulation and debug actions; review standalone GX import |
| `full` | Delegate the existing external action types after explicit settings confirmation |

Policy definitions and the default are owned by [application.approval](../../src/application/approval.py). Connected Agent candidates use the same policy; standalone MCP returns confirmation-required proposals without executing them. Details are in [MCP](../integrations/mcp.md).

## Changes and queued work

Policy writes require the expected revision and an operator credential. Raising permissions does not execute old pending requests. Before an external effect, queued work checks the policy and frozen proposal inputs again. Lowering permissions stops an unstarted delegated action; it cannot undo an operation already in progress.

Validation, artifact bindings and workspace/desktop locks apply in every mode. The ordinary Web CSV send also retains its manual-backup confirmation, described in [GX Works2 operations](../guides/gxworks2.md).

HTTP session and request protections are documented in [HTTP reference](../integrations/http-api.md). Read-only startup is a recovery option, documented in [getting started](../guides/getting-started.md).
