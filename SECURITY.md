# Security and private information

Do not disclose credentials, private implementation, engineering files or research material in public issues. For a vulnerability, use GitHub's private vulnerability reporting when it is available on this repository; otherwise obtain a private contact channel from the maintainer before sharing details.

The Web workbench uses an authenticated local loopback backend. MCP runs independently by default and can optionally bridge to that backend. Keep operator tokens private and preserve origin, session, CSRF and approval checks. Download compiled components from the project's Releases; this source tree does not contain the private core.
