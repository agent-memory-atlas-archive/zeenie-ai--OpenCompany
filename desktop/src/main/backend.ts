/**
 * Spawn + readiness for the uvicorn backend (docs-internal/desktop_host_contract.md §3-4).
 */

import { type ChildProcess, spawn } from "node:child_process";
import { mkdirSync, openSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { Layout } from "./paths";

export interface ReadyBody {
  ready: boolean;
  phase: string;
  database?: boolean;
  version?: string;
  temporal?: { enabled: boolean; phase: string; client_connected: boolean; worker_ready: boolean; pool_ready: boolean };
}

export interface SpawnBackendOptions {
  layout: Layout;
  python: string;
  port: number;
  env: NodeJS.ProcessEnv;
}

/**
 * Seconds uvicorn waits for open connections / in-flight handler tasks
 * before running the lifespan shutdown. Its default is "forever": when the
 * renderer's WebSocket died with a reset rather than a clean close, the
 * handler task lingered and uvicorn never reached the lifespan, so the
 * shell's 30 s budget expired and the backend had to be tree-killed. Bound
 * it; the lifespan teardown (which reaps Temporal / the bun sidecar / edgymeow) is what
 * actually matters and still runs after this window.
 */
export const GRACEFUL_SHUTDOWN_SECONDS = 5;

export function backendArgv(port: number): string[] {
  return [
    "-m",
    "uvicorn",
    "main:app",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--log-level",
    "warning",
    "--timeout-graceful-shutdown",
    String(GRACEFUL_SHUTDOWN_SECONDS),
  ];
}

export function spawnBackend(opts: SpawnBackendOptions): ChildProcess {
  const { layout, python, port, env } = opts;
  mkdirSync(layout.logsDir, { recursive: true });
  // A file handle, not an undrained pipe: import-time crashes that happen
  // before the backend configures its own logger land here too.
  const out = openSync(join(layout.logsDir, "backend.stdout.log"), "a");
  const child = spawn(python, backendArgv(port), {
    cwd: layout.serverDir,
    env,
    // stdin stays a pipe the shell keeps open: EOF is the backend's fast
    // "parent is gone" signal (OPENCOMPANY_DESKTOP_STDIN=1).
    stdio: ["pipe", out, out],
    windowsHide: true,
    // POSIX: own process group so a SIGKILL fallback reaches Temporal/node too.
    detached: layout.platform !== "win32",
  });
  if (child.pid) writeFileSync(layout.backendPidFile, String(child.pid));
  return child;
}

export function readyUrl(port: number): string {
  return `http://127.0.0.1:${port}/health/ready`;
}

export async function fetchReady(port: number, timeoutMs = 2000): Promise<ReadyBody | null> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(readyUrl(port), { signal: ctl.signal });
    const body = (await res.json()) as ReadyBody;
    return body;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export interface WaitReadyOptions {
  port: number;
  timeoutMs: number;
  intervalMs?: number;
  onPhase?: (phase: string, body: ReadyBody | null) => void;
  /** Return true to abort waiting (child died). */
  aborted?: () => boolean;
}

/** Human text for the splash, keyed by the backend's coarse phase strings. */
export function phaseLabel(phase: string): string {
  switch (phase) {
    case "starting":
      return "Starting backend...";
    case "installing_temporal":
      return "Downloading the Temporal engine (first run, about 114 MB)...";
    case "starting_temporal":
      return "Starting the Temporal engine...";
    case "connecting":
      return "Connecting to the Temporal engine...";
    case "starting_workers":
      return "Starting workflow workers...";
    case "ready":
      return "Ready.";
    case "disabled":
      return "Ready (Temporal disabled).";
    default:
      return phase;
  }
}

export async function waitReady(opts: WaitReadyOptions): Promise<ReadyBody> {
  const interval = opts.intervalMs ?? 250;
  const deadline = Date.now() + opts.timeoutMs;
  let lastPhase = "";
  while (Date.now() < deadline) {
    if (opts.aborted?.()) throw new Error("backend exited before becoming ready");
    const body = await fetchReady(opts.port);
    const phase = body?.phase ?? "starting";
    if (phase !== lastPhase) {
      lastPhase = phase;
      opts.onPhase?.(phase, body);
    }
    if (body?.ready) return body;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error(`backend not ready after ${Math.round(opts.timeoutMs / 1000)}s (last phase: ${lastPhase || "unknown"})`);
}
