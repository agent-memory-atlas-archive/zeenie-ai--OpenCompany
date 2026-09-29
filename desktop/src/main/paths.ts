/**
 * Every on-disk location the shell touches, derived from two roots:
 *
 *   resources/  read-only, shipped inside the app bundle (electron-builder
 *               extraResources) or, in development, `desktop/stage/`.
 *     app-root/           backend sibling layout -> OPENCOMPANY_APP_ROOT
 *     runtime/{uv,python,bun}
 *   userData/   Electron's per-user writable dir (%APPDATA%/OpenCompany,
 *               ~/Library/Application Support/OpenCompany, ~/.config/OpenCompany)
 *     pyenv/venv          UV_PROJECT_ENVIRONMENT (the backend interpreter)
 *     pyenv/python        UV_PYTHON_INSTALL_DIR (fallback path only)
 *     pyenv/cache         UV_CACHE_DIR
 *     pyenv/tools         UV_TOOL_DIR / bin (the browser-use CLI)
 *     pycache/            PYTHONPYCACHEPREFIX (the bundle is read-only)
 *     bun/                BUN_INSTALL (bun's package cache for the plugin CLIs)
 *     logs/
 *     desktop-state.json  persisted port etc.
 *     desktop.env         operator overrides -> OPENCOMPANY_ENV_FILE
 *
 * `DATA_DIR` (~/.opencompany) is deliberately NOT here: the backend owns it
 * and shares it with CLI installs.
 *
 * Pure: no Electron imports, so unit tests can exercise it.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

export interface Layout {
  platform: NodeJS.Platform;
  resources: string;
  appRoot: string;
  serverDir: string;
  clientDist: string;
  runtimeDir: string;
  uvBin: string;
  pythonBin: string;
  /** The bundled bun: the JS executor runtime and the installer for the npm-registry CLIs. */
  bunDir: string;
  bunBin: string;
  /** BUN_INSTALL for the backend: bun's package cache and global state, under userData. */
  bunHomeDir: string;
  userData: string;
  pyenvDir: string;
  venvDir: string;
  pythonInstallDir: string;
  uvCacheDir: string;
  uvToolDir: string;
  uvToolBinDir: string;
  pycacheDir: string;
  logsDir: string;
  stateFile: string;
  userEnvFile: string;
  backendPidFile: string;
}

export interface LayoutOptions {
  resources: string;
  userData: string;
  platform?: NodeJS.Platform;
  /** Override the staged app-root (dev: point at the repo checkout). */
  appRoot?: string;
  /** Override the runtime dir (dev: `stage/runtime/<os>-<arch>`). */
  runtimeDir?: string;
}

export function electronOs(platform: NodeJS.Platform): "win" | "mac" | "linux" {
  return platform === "win32" ? "win" : platform === "darwin" ? "mac" : "linux";
}

export function targetKey(platform: NodeJS.Platform, arch: string): string {
  return `${electronOs(platform)}-${arch === "arm64" ? "arm64" : "x64"}`;
}

export function venvPython(venvDir: string, platform: NodeJS.Platform): string {
  return platform === "win32" ? join(venvDir, "Scripts", "python.exe") : join(venvDir, "bin", "python");
}

export function bundledPython(runtimeDir: string, platform: NodeJS.Platform): string {
  return platform === "win32" ? join(runtimeDir, "python", "python.exe") : join(runtimeDir, "python", "bin", "python3");
}

export function resolveLayout(opts: LayoutOptions): Layout {
  const platform = opts.platform ?? process.platform;
  const resources = opts.resources;
  const appRoot = opts.appRoot ?? join(resources, "app-root");
  const runtimeDir = opts.runtimeDir ?? join(resources, "runtime");
  const win = platform === "win32";
  const bunDir = join(runtimeDir, "bun");
  const pyenvDir = join(opts.userData, "pyenv");
  return {
    platform,
    resources,
    appRoot,
    serverDir: join(appRoot, "server"),
    clientDist: join(appRoot, "client", "dist"),
    runtimeDir,
    uvBin: join(runtimeDir, "uv", win ? "uv.exe" : "uv"),
    pythonBin: bundledPython(runtimeDir, platform),
    bunDir,
    bunBin: join(bunDir, win ? "bun.exe" : "bun"),
    bunHomeDir: join(opts.userData, "bun"),
    userData: opts.userData,
    pyenvDir,
    venvDir: join(pyenvDir, "venv"),
    pythonInstallDir: join(pyenvDir, "python"),
    uvCacheDir: join(pyenvDir, "cache"),
    uvToolDir: join(pyenvDir, "tools"),
    uvToolBinDir: join(pyenvDir, "tools", "bin"),
    pycacheDir: join(opts.userData, "pycache"),
    logsDir: join(opts.userData, "logs"),
    stateFile: join(opts.userData, "desktop-state.json"),
    userEnvFile: join(opts.userData, "desktop.env"),
    backendPidFile: join(opts.userData, "backend.pid"),
  };
}

/** Which shipped pieces are actually present (dev checkouts may lack runtimes). */
export function layoutReport(layout: Layout): Record<string, boolean> {
  return {
    appRoot: existsSync(join(layout.serverDir, "main.py")),
    clientDist: existsSync(join(layout.clientDist, "index.html")),
    envTemplate: existsSync(join(layout.appRoot, ".env.template")),
    uv: existsSync(layout.uvBin),
    python: existsSync(layout.pythonBin),
    bun: existsSync(layout.bunBin),
  };
}
