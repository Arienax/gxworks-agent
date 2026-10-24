"""Public product identity shared by the launchers and MCP metadata."""

PROJECT_URL = "https://github.com/Arienax/gxworks-agent"

# The MCP SDK adds this identity to tool results. Delivery accounting uses the
# same value so the final client-visible envelope stays within the read limit.
MCP_SERVER_INFO = {
    'name': 'gxworks-agent', 'title': 'GXWorks Agent', 'version': '0.1.0',
    'description': f'Local PLC engineering tools. Project source and releases: {PROJECT_URL}',
    'websiteUrl': PROJECT_URL,
}
