import { win32 } from 'node:path'

export const name = 'gxworks-windows-sdk'

export function apply(ctx) {
  const snapshotRoot = win32.parse(process.pkg?.entrypoint ?? '').root
  if (!snapshotRoot) return

  // Node can resolve a SEA native import under the working directory's drive.
  // pkg only extracts addons under its canonical virtual snapshot drive.
  const dlopen = process.dlopen
  ctx.effect(() => {
    const loadAddon = function (module, filename, ...flags) {
      const path = filename.startsWith('\\\\?\\') ? filename.slice(4) : filename
      const relative = path.slice(win32.parse(path).root.length)
      if (relative.startsWith('snapshot\\') && relative.endsWith('\\@koromix\\koffi-win32-x64\\win32_x64\\koffi.node')) {
        filename = win32.join(snapshotRoot, relative)
      }
      return dlopen.call(this, module, filename, ...flags)
    }
    process.dlopen = loadAddon
    return () => {
      if (process.dlopen === loadAddon) process.dlopen = dlopen
    }
  })
}
