import { createWriteStream, existsSync } from "node:fs";
import { mkdir, mkdtemp, rename, rm } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { pipeline } from "node:stream/promises";
import { parseArgs } from "node:util";
import yauzl from "yauzl";

export const projectUrl = "https://github.com/Arienax/gxworks-agent";
export const runtimeTag = "v0.1.0-20261014";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const executableName = "GXWorks-Agent-Web.exe";
const packageFolder = "GXWorks-Agent-Web";
const archiveUrl = `${projectUrl}/releases/download/${runtimeTag}/GXWorks-Agent-Web-windows-x64.zip`;

export function defaultWorkspace(env = process.env, home = homedir()) {
  return path.resolve(env.LOCALAPPDATA || path.join(home, "AppData", "Local"), "GXWorks Agent", "web-workspace");
}

export function safeEntry(base, name, attributes = 0) {
  const parts = name.split("/");
  if (parts[0] !== packageFolder || parts.some(part => part === ".." || part === "." || /[\\:\x00]/.test(part))
      || parts.some(part => /[. ]$/.test(part) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part))
      || ((attributes >>> 16) & 0o170000) === 0o120000) throw new Error("Unsafe archive entry");
  const target = path.resolve(base, ...parts);
  if (!target.startsWith(path.resolve(base) + path.sep)) throw new Error("Archive escaped staging directory");
  return target;
}

export async function extractRuntime(archive, stage) {
  const zip = await yauzl.openPromise(archive, { lazyEntries: true, strictFileNames: true, validateEntrySizes: true });
  let bytes = 0, count = 0;
  const names = new Set();
  try {
    for await (const entry of zip.eachEntry()) {
      const target = safeEntry(stage, entry.fileName, entry.externalFileAttributes);
      const key = target.toLowerCase();
      if (names.has(key)) throw new Error("Duplicate archive entry");
      names.add(key);
      bytes += entry.uncompressedSize;
      if (++count > 30000 || bytes > 3 * 1024 ** 3) throw new Error("Runtime archive exceeds size limit");
      if (entry.fileName.endsWith("/")) { await mkdir(target, { recursive: true }); continue; }
      await mkdir(path.dirname(target), { recursive: true });
      await pipeline(await zip.openReadStreamPromise(entry), createWriteStream(target, { flags: "wx" }));
    }
  } finally { zip.close(); }
  if (!existsSync(path.join(stage, packageFolder, executableName))) throw new Error("Archive has no Web runtime");
}

async function download(target) {
  let url = archiveUrl;
  for (let redirects = 0; redirects < 6; redirects++) {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port
        || !["github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"].includes(parsed.hostname)) {
      throw new Error("Unexpected runtime download address");
    }
    const response = await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(15 * 60 * 1000) });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const next = response.headers.get("location");
      await response.body?.cancel();
      if (!next) throw new Error("Missing release redirect");
      url = new URL(next, url).href; continue;
    }
    if (!response.ok || !response.body) throw new Error(`Web runtime download failed (${response.status}); see ${projectUrl}/releases`);
    let size = 0;
    await pipeline(response.body, async function* (stream) {
      for await (const chunk of stream) {
        size += chunk.length;
        if (size > 1024 ** 3) throw new Error("Download exceeds size limit");
        yield chunk;
      }
    }, createWriteStream(target, { flags: "wx" }));
    return;
  }
  throw new Error("Too many download redirects");
}

export async function ensureRuntime({ archive } = {}) {
  const bundled = path.join(root, executableName);
  if (!archive && existsSync(bundled)) return bundled;
  const cache = path.join(root, ".runtime");
  const installed = path.join(cache, runtimeTag, packageFolder, executableName);
  if (existsSync(installed)) return installed;
  await mkdir(cache, { recursive: true });
  const stage = await mkdtemp(path.join(cache, ".install-"));
  try {
    const input = archive ? path.resolve(archive) : path.join(stage, "download.zip");
    if (!archive) {
      console.log(`首次启动：下载 Web 核心组件 ${runtimeTag}\n${archiveUrl}`);
      await download(input);
    }
    const extracted = path.join(stage, "unpacked");
    await mkdir(extracted);
    await extractRuntime(input, extracted);
    try { await rename(extracted, path.join(cache, runtimeTag)); }
    catch (error) { if (!existsSync(installed)) throw error; }
    return installed;
  } finally {
    // Only this process's fresh staging directory can be removed.
    if (path.dirname(stage) !== cache || !path.basename(stage).startsWith(".install-")) throw new Error("Unsafe cleanup path");
    await rm(stage, { recursive: true, force: true });
  }
}

async function availablePort(requested) {
  const probe = port => new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => { const value = server.address().port; server.close(() => resolve(value)); });
  });
  if (requested !== undefined) {
    const port = Number(requested);
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid port");
    return probe(port);
  }
  try { return await probe(8765); } catch (error) { if (error.code !== "EADDRINUSE") throw error; return probe(0); }
}

export async function main(argv = process.argv.slice(2)) {
  const command = argv.shift() || "start";
  const { values } = parseArgs({ args: argv, options: {
    workspace: { type: "string" }, port: { type: "string" }, archive: { type: "string" },
    "state-dir": { type: "string" }, "no-browser": { type: "boolean" },
    "read-only": { type: "boolean" }, "no-service-discovery": { type: "boolean" },
  } });
  if (!["setup", "start"].includes(command)) throw new Error("Use npm run setup or npm start");
  if (process.platform !== "win32" || process.arch !== "x64") throw new Error("The compiled runtime requires Windows x64; frontend builds are cross-platform.");
  const staticDir = path.join(root, "web", "dist");
  if (command === "start" && !existsSync(path.join(staticDir, "index.html"))) throw new Error("请先运行 npm run build。");
  const executable = await ensureRuntime(values);
  if (command === "setup") { console.log("Web 核心组件已就绪。"); return; }
  const workspace = values.workspace ? path.resolve(values.workspace) : defaultWorkspace();
  const port = await availablePort(values.port);
  const args = ["--workspace", workspace, "--static-dir", staticDir, "--port", String(port)];
  if (values["state-dir"]) args.push("--state-dir", path.resolve(values["state-dir"]));
  for (const flag of ["read-only", "no-service-discovery"]) if (values[flag]) args.push("--" + flag);
  if (!values["no-browser"]) args.push("--open-browser");
  console.log(`GXWorks Agent · ${projectUrl}\n工程目录：${workspace}`);
  const child = spawn(executable, args, { stdio: "inherit", windowsHide: true });
  const stop = () => child.kill();
  process.once("SIGINT", stop); process.once("SIGTERM", stop);
  try {
    const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });
    process.exitCode = code ?? 1;
  } finally { process.removeListener("SIGINT", stop); process.removeListener("SIGTERM", stop); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
