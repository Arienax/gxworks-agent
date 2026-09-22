"""GXWorks Agent MCP launcher.

Normal Web users connect through the running local application service.  On
Windows the Web launcher publishes the loopback origin, unprivileged Agent
credential and current project binding to Credential Manager, so normal clients
need no token, URL, workspace or PYTHONPATH configuration.

The SessionStore reader remains available as explicit --standalone/headless
mode.  An explicitly supplied legacy --workspace still selects standalone mode
for backward compatibility.
"""

import argparse
import json
import logging
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path


class _StderrParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        super()._print_message(message, sys.stderr)


def _has_option(raw_args, name):
    return any(value == name or value.startswith(name + "=") for value in raw_args)


def _service_connection(args, raw_args, parser):
    from .service_credentials import load_service_binding
    from .service_client import validate_service_url

    token = os.environ.get(args.service_token_env, "").strip()
    # A fully specified headless connection must not touch local credentials.
    binding = {} if args.service_url and token and (args.project or args.check) else (load_service_binding() or {})
    explicit_url = bool(args.service_url)
    service_url = args.service_url or binding.get("service_url") or "http://127.0.0.1:8765"
    service_url = validate_service_url(service_url)
    matching_binding = binding if binding.get("service_url") == service_url else {}
    if not token and binding:
        bound_url = validate_service_url(binding.get("service_url"))
        if explicit_url and bound_url != service_url:
            parser.error("The saved MCP credential belongs to another local Web service. Start that service or provide the matching token environment variable.")
        if bound_url == service_url:
            token = str(binding.get("agent_token") or "").strip()
    if not token:
        parser.error(
            "No local MCP credential was found. Start GXWorks Agent Web once to publish its credential, "
            f"or set {args.service_token_env} explicitly."
        )
    # A project bound to another origin is never a default for this service.
    project_id = args.project or matching_binding.get("project_id")
    return service_url, token, project_id


def main(argv=None) -> int:
    raw_args = list(argv) if argv is not None else list(sys.argv[1:])
    parser = _StderrParser(description="GXWorks Agent MCP server (Python 3.10+).")
    parser.add_argument("--stdio", action="store_true", help="Use stdio (the current MCP transport and default).")
    parser.add_argument("--check", action="store_true", help="Verify local service discovery/authentication and print a small JSON result instead of starting MCP stdio.")
    parser.add_argument(
        "--standalone", action="store_true",
        help="Advanced/headless mode: read an existing SessionStore directly instead of the running Web service.",
    )
    parser.add_argument(
        "--workspace", type=Path,
        default=os.environ.get("PLC_AI_WORKSPACE_DIR", "").strip() or None,
        help="Standalone only: existing SessionStore workspace; defaults to PLC_AI_WORKSPACE_DIR.",
    )
    parser.add_argument("--project", help="Project ID. Normal Web mode can use the project bound by Integrations / MCP.")
    parser.add_argument("--version", help="Pin a version ID; otherwise follow the current active version.")
    parser.add_argument(
        "--service-url",
        default=os.environ.get("GXWORKS_AGENT_SERVICE_URL", "").strip() or None,
        help="Optional explicit loopback Web origin. Normally discovered automatically from the running Web workbench.",
    )
    parser.add_argument(
        "--service-token-env",
        default=os.environ.get("GXWORKS_AGENT_TOKEN_ENV", "").strip() or "PLC_WEB_AGENT_TOKEN",
        help="Optional environment variable containing the Agent token. Normal Windows use reads Credential Manager instead.",
    )
    args = parser.parse_args(raw_args)
    explicit_workspace = _has_option(raw_args, "--workspace")
    standalone = bool(args.standalone or explicit_workspace)
    if standalone and args.workspace is None:
        parser.error("--standalone requires --workspace or PLC_AI_WORKSPACE_DIR")
    if standalone and not args.project:
        parser.error("standalone mode requires --project")
    if standalone and (_has_option(raw_args, "--service-url") or _has_option(raw_args, "--service-token-env") or args.check):
        parser.error("standalone mode cannot be combined with service connection/check options")
    if sys.version_info < (3, 10):
        parser.error("MCP requires Python 3.10+")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    try:
        with redirect_stdout(sys.stderr):
            if standalone:
                import anyio
                from .context_provider import SessionToolContextProvider
                from .server import serve_stdio

                provider = SessionToolContextProvider(args.workspace, args.project, args.version)
                runner, runner_args = serve_stdio, (provider,)
                check_result = None
            else:
                from .service_client import ApplicationServiceClient

                service_url, token, project_id = _service_connection(args, raw_args, parser)
                client = ApplicationServiceClient(service_url, token)
                if args.check:
                    tools = client.list_tools()
                    check_result = {
                        "ok": True,
                        "service_url": service_url,
                        "project_id": project_id,
                        "tool_count": len(tools),
                    }
                    runner = runner_args = None
                else:
                    import anyio
                    from .service_client import ServiceMCPToolAdapter
                    from .server import serve_service_stdio

                    if not project_id:
                        parser.error("No MCP project is bound. Open Web → Settings → Integrations / MCP and connect the current project once.")
                    ServiceMCPToolAdapter(client, project_id, args.version)
                    runner, runner_args = serve_service_stdio, (client, project_id, args.version)
                    check_result = None
    except ImportError:
        parser.error("Install the optional dependencies: python -m pip install -r requirements/mcp.txt")
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))

    if args.check:
        # --check is an explicit diagnostic command, not an MCP protocol stream.
        print(json.dumps(check_result, ensure_ascii=False))
        return 0
    try:
        anyio.run(runner, *runner_args)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
