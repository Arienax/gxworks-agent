# Private component integration

The browser uses relative `/api` URLs. `web/openapi.json` and `web/src/api/generated.ts` describe the public HTTP boundary; they contain no backend implementation. Frontend types can be regenerated with `npm run types` from the `web` directory.

For local development, start a compatible private backend on `http://127.0.0.1:8765` and run the frontend on `http://127.0.0.1:5173`. The existing development proxy forwards API requests and translates only the expected local frontend Origin. Operator sessions, cookies, CSRF checks, approvals and resource locks remain enforced by the backend. Do not put operator tokens or model credentials in source files, environment files committed to Git, or browser build variables.

Use the operator token supplied by that local backend to log in. A backend login link can also be opened locally with its port changed to `5173`, retaining the fragment. Keep the token and link private. API event streams use the same proxy.

For a packaged private installation, serve the built `web/dist` assets and API under one loopback origin. The backend operator owns deployment and version compatibility. This repository does not supply the private component, a public download location, a hosted API or a standalone MCP launcher.

An unavailable backend is reported as a connection error. Engineering functions require the backend and any applicable local software. The frontend does not implement fallback PLC generation, project parsing or device execution.

Publishing UI code and interface types reveals the user-facing protocol. Keeping the implementation private prevents this repository from distributing it; it does not make a separately distributed executable impossible to analyze.
