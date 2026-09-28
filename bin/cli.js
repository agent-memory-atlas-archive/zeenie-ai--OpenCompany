#!/usr/bin/env bun
// The `company` launcher. Installed by `bun add -g @zeenie-ai/opencompany`
// and run by bun (the shebang). The code itself is plain JavaScript, so it
// also parses under node, but the paths that need bun say so instead of
// falling back to npm.

import { spawn, spawnSync, execSync } from 'child_process';
import { basename, dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { readFileSync, existsSync } from 'fs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PKG = JSON.parse(readFileSync(resolve(ROOT, 'package.json'), 'utf-8'));
const invokedName = basename(process.argv[1] || '').replace(/\.(?:cmd|exe|ps1|js)$/i, '');
const USING_LEGACY_ALIAS = invokedName === 'machina'
  || process.env.OPENCOMPANY_LEGACY_ALIAS === 'machina';

const COMMANDS = {
  start: 'Start in production mode',
  dev: 'Start development server (hot-reload)',
  serve: 'Serve on a single public port (API + WS + SPA; used by deploy)',
  daemon: 'Run the backend detached (start|stop|status|restart)',
  deploy: 'Provision a cloud VM running OpenCompany (Terraform)',
  stop: 'Stop all running services',
  build: 'Build the project for production',
  clean: 'Clean build artifacts',
  docs: 'Documentation tooling (docs nodes [--check])',
  doctor: 'Check system dependencies and project health',
  provision: 'Set up the Python environment (runs automatically on first use; --force re-runs)',
  help: 'Show this help message',
  version: 'Show version number (version sync [tag] to sync from a git tag)',
};

// Verbs whose Python implementation is a Typer sub-app: they take a sub-verb
// and flags, so every remaining argv entry has to be forwarded rather than
// dropped by the bare ``run(cmd)`` path.
const SUBCOMMAND_VERBS = new Set(['daemon', 'deploy', 'docs', 'version']);

function printHelp() {
  console.log(`
OpenCompany - Self-improving AI employees

Usage: company <command> [flags]

Commands:
${Object.entries(COMMANDS).map(([cmd, desc]) => `  ${cmd.padEnd(14)} ${desc}`).join('\n')}

Flags:
  --daemon         Bind the backend to 0.0.0.0 instead of 127.0.0.1 (dev)

Examples:
  company start          # Production server (clean output)
  company dev            # Development with hot-reload
  company build          # Build for production

Compatibility: the legacy \`machina\` command remains available but is deprecated.
Documentation: https://docs.opencompany.sh/
`);
}

// bun is the package manager, script runner and JS runtime everywhere.
// There is no npm fallback: without bun on PATH, the paths below that need
// it stop with a pointer to https://bun.sh.
function hasBun() {
  return getVersion('bun --version') !== null;
}

function isSourceCheckout() {
  // bunfig.toml is committed but excluded from the published tarball.
  return existsSync(resolve(ROOT, 'bunfig.toml'));
}

function getVersion(cmd) {
  try {
    return execSync(cmd, { encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
  } catch {
    return null;
  }
}

// Add common binary paths to PATH (Linux installs uv to ~/.local/bin)
function expandPath() {
  const home = process.env.HOME || process.env.USERPROFILE;
  if (home) {
    const additionalPaths = [
      `${home}/.local/bin`,      // uv, cargo installs
      `${home}/.cargo/bin`,      // Rust tools
      '/usr/local/bin',          // Homebrew on macOS
    ];
    const currentPath = process.env.PATH || '';
    const sep = process.platform === 'win32' ? ';' : ':';
    const newPaths = additionalPaths.filter(p => !currentPath.includes(p));
    if (newPaths.length > 0) {
      process.env.PATH = newPaths.join(sep) + sep + currentPath;
    }
  }
}

function checkDeps() {
  const errors = [];

  // Python version check
  let pyVersion = getVersion('python --version') || getVersion('python3 --version');
  if (!pyVersion) {
    errors.push('Python 3.12+ - https://python.org/');
  } else {
    const match = pyVersion.match(/Python (\d+)\.(\d+)/);
    if (match) {
      const [, major, minor] = match.map(Number);
      if (major < 3 || (major === 3 && minor < 12)) {
        errors.push(`Python 3.12+ required (found ${pyVersion})`);
      }
    }
  }

  // uv package manager check
  if (!getVersion('uv --version')) {
    errors.push('uv (Python package manager) - https://docs.astral.sh/uv/');
  }

  if (errors.length > 0) {
    console.error('Missing required dependencies:\n' + errors.map(e => `  - ${e}`).join('\n'));
    console.error('\nInstall the missing dependencies and try again.');
    process.exit(1);
  }
}

function doctor() {
  console.log('\nOpenCompany Doctor\n');
  if (hasBun()) {
    try {
      execSync('bun x envinfo --system --binaries', { cwd: ROOT, stdio: 'inherit', shell: true });
    } catch { /* envinfo not available, continue with manual checks */ }
  } else {
    console.log('  bun: Not Found (required: https://bun.sh)\n');
  }

  console.log('  Additional checks:');
  const checks = [
    ['uv', getVersion('uv --version')],
    ['temporal', getVersion('temporal --version')],
  ];
  for (const [name, ver] of checks) {
    console.log(ver ? `    ${name}: ${ver}` : `    ${name}: Not Found`);
  }

  const has = (f) => { try { readFileSync(resolve(ROOT, f)); return true; } catch { return false; } };
  console.log(has('bun.lock') ? '    Lockfile: bun.lock' : has('package-lock.json') ? '    Lockfile: package-lock.json' : '    Lockfile: Not Found');
  console.log(has('server/.venv/pyvenv.cfg') ? '    Python venv: OK' : '    Python venv: Missing (run company build)');
  console.log('');
}

// Resolve <ROOT>/.cli-venv Python if provisioning has run. Returns null on a
// fresh global install before its first run, and on a source checkout whose
// `bun install` skipped the postinstall hook (which runs scripts/install.js,
// the step that creates the venv); `run()` then falls back to `bun run`.
function venvPython() {
  const py = process.platform === 'win32'
    ? resolve(ROOT, '.cli-venv', 'Scripts', 'python.exe')
    : resolve(ROOT, '.cli-venv', 'bin', 'python');
  return existsSync(py) ? py : null;
}

// The app port from the shipped .env.template (the single place ports live).
function appPort() {
  try {
    const m = /^PYTHON_BACKEND_PORT=(\d+)/m.exec(readFileSync(resolve(ROOT, '.env.template'), 'utf-8'));
    return m ? m[1] : '';
  } catch {
    return '';
  }
}

// Run scripts/install.js (uv, server venv, bytecode, CLI venv, Temporal)
// under this runtime. Exits the process on failure.
function runInstallJs() {
  const installJs = resolve(ROOT, 'scripts', 'install.js');
  if (!existsSync(installJs)) {
    console.error('scripts/install.js is missing from this install; reinstall with: bun add -g @zeenie-ai/opencompany');
    process.exit(1);
  }
  const result = spawnSync(process.execPath, [installJs], {
    cwd: ROOT,
    stdio: 'inherit',
    env: { ...process.env, FORCE_COLOR: '1' },
  });
  if (result.status !== 0) {
    console.error('\nProvisioning failed. Fix the error above and re-run: company provision');
    process.exit(result.status || 1);
  }
}

// First-run provisioning for a global install. `bun add -g` does not run a
// dependency's lifecycle scripts, so the venv the postinstall hook used to
// create is built here, on the first `company` command. Source checkouts
// provision through `company build` instead.
function ensureProvisioned() {
  if (venvPython() || isSourceCheckout()) return;
  console.log('First run: provisioning the Python environment (one time)...');
  runInstallJs();
}

// `company provision [--force]`: the explicit form of the above, for the
// installers (which cannot know where bun placed the package, while this
// file always knows its own ROOT) and for repairing a broken install.
function provision(force) {
  if (isSourceCheckout()) {
    console.log('This is a source checkout; run: company build');
    return;
  }
  if (venvPython() && !force) {
    console.log('Already provisioned (run `company provision --force` to redo it).');
  } else {
    runInstallJs();
  }
  const port = appPort();
  console.log('');
  console.log('Run: company start');
  if (port) console.log(`Open: http://localhost:${port}`);
  console.log('');
}

function run(script, extraArgs = []) {
  // Global-install fast path: spawn the venv's Python directly with
  // ``-m cli <cmd>``. Skips the script-runner hop that previously re-
  // resolved the system ``python`` (which on PEP 668 systems lacks
  // the CLI runtime deps -- typer/rich/anyio/psutil). The script-runner
  // path stays as the fallback when no venv exists (see venvPython):
  // ``bun run <script>``. It needs bun on PATH; without it the command
  // stops with a pointer to bun rather than trying npm.
  ensureProvisioned();
  const venvPy = venvPython();
  if (venvPy) {
    const child = spawn(venvPy, ['-m', 'cli', script, ...extraArgs], {
      cwd: ROOT,
      stdio: 'inherit',
    });
    child.on('error', (e) => { console.error(`Failed: ${e.message}`); process.exit(1); });
    child.on('close', (code) => process.exit(code || 0));
    return;
  }

  if (!hasBun()) {
    console.error('OpenCompany requires bun to run this command. Install it: https://bun.sh');
    process.exit(1);
  }
  const runnerArgs = ['run', script];
  if (extraArgs.length) runnerArgs.push('--', ...extraArgs);
  const runner = process.platform === 'win32' ? 'bun.exe' : 'bun';
  const child = spawn(runner, runnerArgs, {
    cwd: ROOT,
    stdio: 'inherit',
    shell: true,
  });
  child.on('error', (e) => { console.error(`Failed: ${e.message}`); process.exit(1); });
  child.on('close', (code) => process.exit(code || 0));
}

// Expand PATH to find tools like uv installed in user directories
expandPath();

if (USING_LEGACY_ALIAS) {
  console.warn('Warning: `machina` is deprecated; use `company` instead.');
}

const cmd = process.argv[2] || 'help';

const rest = process.argv.slice(3);

if (cmd === 'help' || cmd === '--help' || cmd === '-h') {
  printHelp();
} else if (cmd === '--version' || cmd === '-v' || (cmd === 'version' && rest.length === 0)) {
  // Bare `version` stays a local, dependency-free print. With a sub-verb
  // (`version sync`) it is the Typer sub-app instead -- previously that
  // silently printed the version and discarded `sync`.
  console.log(`company v${PKG.version}`);
} else if (cmd === 'doctor') {
  doctor();
} else if (cmd === 'provision') {
  provision(rest.includes('--force'));
} else if (cmd === 'start' || cmd === 'dev' || cmd === 'build' || cmd === 'serve') {
  // Provision before the dependency check: on a fresh global install uv is
  // installed BY provisioning (scripts/install.js), so checking for it first
  // would fail the very first `company start`.
  ensureProvisioned();
  checkDeps();
  run(cmd, rest);
} else if (SUBCOMMAND_VERBS.has(cmd)) {
  // Sub-app verbs: forward the sub-verb and every flag verbatim.
  run(cmd, rest);
} else if (COMMANDS[cmd]) {
  run(cmd, rest);
} else {
  console.error(`Unknown command: ${cmd}`);
  printHelp();
  process.exit(1);
}
