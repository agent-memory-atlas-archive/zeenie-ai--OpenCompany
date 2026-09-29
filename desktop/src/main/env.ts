/**
 * The environment the backend is spawned with — the shell's half of
 * docs-internal/desktop_host_contract.md.
 *
 * Precedence (low -> high): the shell's own process env (minus a few Python /
 * Electron keys), then the contract values below, then the operator's
 * `desktop.env` lines for every key outside LOCKED_KEYS. PATH is computed
 * last from the inherited PATH, so desktop.env cannot replace it. The backend
 * then layers .env.template < OPENCOMPANY_ENV_FILE under all of that with
 * setdefault semantics, so a key set here always wins over the template.
 *
 * Pure (no Electron imports) so it is unit-testable.
 */

import { existsSync, readFileSync } from "node:fs";
import { delimiter, join } from "node:path";

import type { Layout } from "./paths";

export interface BackendEnvOptions {
  layout: Layout;
  port: number;
  token: string;
  parentPid: number;
  /** The environment to inherit (default: `process.env`); tests pass their own. */
  baseEnv?: NodeJS.ProcessEnv;
}

/** KEY=VALUE parser with the same rules as core/env_defaults._parse_env_file. */
export function parseEnvFile(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const eq = line.indexOf("=");
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === '"' || value[0] === "'")) {
      value = value.slice(1, -1);
    }
    if (key) out[key] = value;
  }
  return out;
}

export function readUserEnvFile(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  try {
    return parseEnvFile(readFileSync(path, "utf-8"));
  } catch {
    return {};
  }
}

/** Keys the operator may not override from desktop.env (they define the contract). */
const LOCKED_KEYS = new Set([
  "OPENCOMPANY_DESKTOP",
  "OPENCOMPANY_PARENT_PID",
  "OPENCOMPANY_DESKTOP_TOKEN",
  "OPENCOMPANY_DESKTOP_STDIN",
  "OPENCOMPANY_APP_ROOT",
  "OPENCOMPANY_CLIENT_DIST",
  "OPENCOMPANY_ENV_FILE",
  "PORT",
  "PYTHON_BACKEND_PORT",
  "HOST",
  "SERVE_STATIC_CLIENT",
  "UV_PROJECT_ENVIRONMENT",
]);

export function prependPath(existing: string | undefined, ...dirs: string[]): string {
  const parts = [...dirs.filter(Boolean), ...(existing ? existing.split(delimiter) : [])];
  const seen = new Set<string>();
  return parts.filter((p) => (seen.has(p) ? false : (seen.add(p), true))).join(delimiter);
}

export function buildBackendEnv(opts: BackendEnvOptions): NodeJS.ProcessEnv {
  const { layout, port, token, parentPid } = opts;
  const base: NodeJS.ProcessEnv = { ...(opts.baseEnv ?? process.env) };
  // A stray active venv on the operator's shell must not leak into uvicorn.
  delete base.VIRTUAL_ENV;
  delete base.PYTHONHOME;
  delete base.PYTHONPATH;
  // Electron marks children it forks; uvicorn is not one of ours in that sense.
  delete base.ELECTRON_RUN_AS_NODE;

  const contract: Record<string, string> = {
    OPENCOMPANY_DESKTOP: "1",
    OPENCOMPANY_PARENT_PID: String(parentPid),
    OPENCOMPANY_DESKTOP_TOKEN: token,
    OPENCOMPANY_DESKTOP_STDIN: "1",
    OPENCOMPANY_APP_ROOT: layout.appRoot,
    OPENCOMPANY_CLIENT_DIST: layout.clientDist,
    OPENCOMPANY_ENV_FILE: layout.userEnvFile,
    PORT: String(port),
    PYTHON_BACKEND_PORT: String(port),
    HOST: "127.0.0.1",
    SERVE_STATIC_CLIENT: "1",
    PYTHONUTF8: "1",
    PYTHONUNBUFFERED: "1",
    PYTHONPYCACHEPREFIX: layout.pycacheDir,
    LOG_FILE: `${layout.logsDir}/backend.log`,
    LOG_FORMAT: "json",
    TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS: "10",
    UV_PROJECT_ENVIRONMENT: layout.venvDir,
    UV_PYTHON_INSTALL_DIR: layout.pythonInstallDir,
    UV_CACHE_DIR: layout.uvCacheDir,
    UV_TOOL_DIR: layout.uvToolDir,
    UV_TOOL_BIN_DIR: layout.uvToolBinDir,
    UV_NO_CONFIG: "1",
    UV_SYSTEM_CERTS: "1",
  };
  if (existsSync(layout.uvBin)) contract.OPENCOMPANY_UV_BIN = layout.uvBin;
  if (existsSync(layout.bunBin)) {
    contract.OPENCOMPANY_BUN_BIN = layout.bunBin;
    // Keep bun's own package cache and global state inside the app's data
    // dir rather than the user's ~/.bun, which may belong to a dev install.
    contract.BUN_INSTALL = layout.bunHomeDir;
    contract.BUN_INSTALL_CACHE_DIR = join(layout.bunHomeDir, "install", "cache");
  }

  const user = readUserEnvFile(layout.userEnvFile);
  for (const [k, v] of Object.entries(user)) {
    if (!LOCKED_KEYS.has(k)) contract[k] = v;
  }

  // PATH: bundled bun and uv first; the bundled bare Python is deliberately
  // NOT added so plugins never pick it over the venv.
  const pathKey = Object.keys(base).find((k) => k.toUpperCase() === "PATH") ?? "PATH";
  const uvDir = existsSync(layout.uvBin) ? layout.uvBin.slice(0, layout.uvBin.lastIndexOf(layout.uvBin.includes("\\") ? "\\" : "/")) : "";
  contract[pathKey] = prependPath(base[pathKey], existsSync(layout.bunBin) ? layout.bunDir : "", uvDir, layout.uvToolBinDir);

  return { ...base, ...contract };
}
