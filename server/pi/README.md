# Private Pi SDK host

This package pins the full Earendil Pi Coding Agent SDK to 1.1.0. It is a
maintenance driver for one assistant response and its tool batch. Model requests
and tool effects are performed by OpenCompany Temporal leaves, never by this
process. The host disables ambient resources, provider networking, SDK retries,
compaction and cache warming.

Run `bun install --frozen-lockfile`, `bun test`, and `bun run typecheck` here.
`bun run build` creates `dist/index.js`, `dist/runtime.tgz`, and
`dist/runtime-manifest.json`. The archive contains the external SDK and complete
private production dependency tree, including package assets and lazy imports.
It needs Bun and tar at build time. No package installation happens at runtime.
The frozen build includes optional dependency binaries for every OS and CPU.
On Unix, extraction restores executable modes for the manifest's relative
`executables` paths; this also supports archives produced on Windows.
Run `bun run test:distribution` after building to exercise the archived host.

`bun run scripts/stage.ts --out <server/pi>` copies the verified distribution
assets into a desktop or server staging directory. The Python driver verifies
the archive SHA-256, extracts it to an immutable runtime directory, and sets
`PI_PACKAGE_DIR` to its `node_modules/@earendil-works/pi-coding-agent` directory.
This archive avoids npm's exclusion of nested `node_modules` directories.

The JSONL protocol is defined in `src/types.ts`. Frames are UTF-8 JSON separated
only by LF, bounded to 256 KiB. Larger values use canonical JSON transport files
in Python's private attempt directory, bounded to 16 MiB. References verify
scope, epoch, content hash, byte count, canonical encoding and regular-file paths.
Stdout contains protocol frames; errors expose
fixed messages without provider exceptions. Each host accepts one `run_turn`.
`keepalive` renews its 120-second input lease; the Python driver sends it every
30 seconds while a durable operation is pending. `abort`, `park`, input EOF and
lease expiry abort the SDK and reap the host. Parking completes maintenance
successfully without committing an aborted transcript.

The model operation references admitted canonical context/configuration/tool
digests. SDK validation produces an `assistant_ready` preflight before tool
callbacks. The Python bridge durably acknowledges this frame before processing
later tool requests or pre-filling a delegation batch. Tool requests reference
the recorded call ID/index, never coerced SDK arguments. Native malformed
arguments, unknown tools, validation failures and truncated calls produce
non-effect receipts. Duplicate or missing recorded call IDs fail before effects.

Canonical `MessageWire` remains authoritative. Pi session entries carry only
conversion metadata, original provider state and durable image references;
hydrated image bytes are rejected. Declaration-only system messages are derived
Pi loadout metadata. A completed snapshot contains the full header, entries,
leaf pointer, scope, epoch, turn, version and canonical projection digest.
Scope mismatch and corruption fail explicitly. Context/configuration/tool
changes rebuild the derived session from canonical wires.

The protocol preserves integer tokens beyond JavaScript's safe-number range as
BigInt in memory and emits them as exact numeric JSON tokens. Unicode key sorting
matches Python's scalar ordering. Pi receives a cloned numeric validation view
of tool arguments and schemas; native Pydantic validation always checks the exact
recorded arguments before any effect.
