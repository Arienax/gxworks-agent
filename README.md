# GXWorks Agent · Public Workbench

[简体中文](README.zh-CN.md)

The public Web interface for GXWorks Agent, a local PLC engineering workbench. This repository contains the React interface, its HTTP contract and frontend tests. The engineering backend is a separate private component and is not provided by this repository.

The interface supports project management, requirements, program views, editing proposals, diagnostics and model settings when connected to a compatible backend. Available operations and CPU support are determined by that backend. Cloning this repository alone does not provide program generation, GXW processing, native GX Works2 integration, simulation or MCP service.

## Run with your private component

Use Node.js 22.14 or newer and npm. Start your separately supplied backend on `http://127.0.0.1:8765`, then:

```powershell
git clone https://github.com/Arienax/gxworks-agent-public.git
cd gxworks-agent-public/web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173` and enter the operator token from your local backend. The development server proxies `/api` to that backend. Without it, the interface cannot load projects or execute engineering operations. No hosted backend or public core download is supplied.

```powershell
npm run build
node --experimental-strip-types --test tests/*.test.mjs
```

The build produces frontend assets in `web/dist`. The private backend operator can serve them with the matching API. `npm run preview` previews static assets only and does not connect the API.

See [private component integration](docs/private-component.md), [publication rules](docs/publishing.md) and [contributing](CONTRIBUTING.md).

## License

Project-authored files retain the [source-available proprietary license](LICENSE). This is not an open-source license. Dependencies retain their respective [licenses and notices](THIRD_PARTY_NOTICES.md).
