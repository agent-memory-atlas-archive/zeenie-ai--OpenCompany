/**
 * Stop the backend the contract way (docs-internal/desktop_host_contract.md §5):
 *
 *   1. POST /api/desktop/shutdown with the per-launch token -> lifespan
 *      teardown reaps Temporal / the bun sidecar / WhatsApp.
 *   2. Close our end of stdin (the backend's fast parent-gone signal).
 *   3. Wait up to `timeoutMs` for exit.
 *   4. Fallback: tree-kill (taskkill /T on Windows, SIGKILL to the process
 *      group elsewhere — the backend was spawned detached).
 *
 * Pure with respect to Electron; `ChildLike` lets tests use a fake child.
 */

import { spawn } from "node:child_process";

export interface ChildLike {
  pid?: number;
  exitCode: number | null;
  killed?: boolean;
  stdin?: { end: () => void; destroyed?: boolean } | null;
  once: (event: "exit" | "close", listener: (...args: unknown[]) => void) => unknown;
  kill: (signal?: NodeJS.Signals | number) => boolean;
}

export interface StopOptions {
  child: ChildLike;
  port: number;
  token: string;
  timeoutMs?: number;
  platform?: NodeJS.Platform;
  /** Injected for tests. */
  post?: (port: number, token: string) => Promise<boolean>;
  treeKill?: (pid: number, platform: NodeJS.Platform) => void;
  log?: (msg: string) => void;
}

export async function postShutdown(port: number, token: string, timeoutMs = 3000): Promise<boolean> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/desktop/shutdown`, {
      method: "POST",
      headers: { "X-Desktop-Token": token },
      signal: ctl.signal,
    });
    return res.status === 202;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export function treeKill(pid: number, platform: NodeJS.Platform = process.platform): void {
  if (platform === "win32") {
    spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    return;
  }
  try {
    process.kill(-pid, "SIGKILL"); // process group (spawned detached)
  } catch {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }
}

export function waitExit(child: ChildLike, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

export type StopOutcome = "already-exited" | "graceful" | "signal" | "tree-killed" | "gone";

export async function stopBackend(opts: StopOptions): Promise<StopOutcome> {
  const { child, port, token } = opts;
  const timeoutMs = opts.timeoutMs ?? 30_000;
  const platform = opts.platform ?? process.platform;
  const post = opts.post ?? postShutdown;
  const kill = opts.treeKill ?? treeKill;
  const log = opts.log ?? (() => undefined);

  if (child.exitCode !== null) return "already-exited";

  const accepted = await post(port, token);
  log(accepted ? "shutdown request accepted" : "shutdown request not accepted; closing stdin");
  try {
    child.stdin?.end();
  } catch {
    /* already closed */
  }
  if (!accepted && platform !== "win32") {
    try {
      child.kill("SIGTERM");
    } catch {
      /* gone */
    }
  }

  if (await waitExit(child, timeoutMs)) return accepted ? "graceful" : "signal";

  if (child.pid) {
    log(`backend still alive after ${timeoutMs}ms; tree-killing pid ${child.pid}`);
    kill(child.pid, platform);
    await waitExit(child, 5000);
    return "tree-killed";
  }
  return "gone";
}
