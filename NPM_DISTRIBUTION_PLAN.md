# NPM Distribution Plan (deferred task)

**Status:** Planned, not started. Written 2026-06-10.
**Trigger to actually build it:** demand from users who run Claude Desktop / other MCP
hosts but do **not** have a Python toolchain. Until that signal appears, `pip install
waystone` + the MCP server cover the real (Python/Claude-Code) audience. Do not build
speculatively — this adds a permanent second release channel to maintain.

## Goal

Let a non-Python user install and run Waystone with `npm install -g waystone` (or, more
usefully, `npx -y waystone-mcp` in an MCP host config) — **without a Python toolchain on
their machine.** The payoff is reach into the JS/MCP ecosystem, where servers are
conventionally added via `npx`.

## Approach: ship prebuilt standalone binaries, wrapped by a thin npm package

This is the esbuild / ripgrep / swc pattern. npm ships JS, so the npm package is only a
launcher; the real artifact is a per-platform standalone binary built from the Python app.

Three layers:

1. **Standalone binary** (no Python required on the user's machine).
2. **GitHub Releases** hosts one binary per platform, attached on each version tag.
3. **npm packages** resolve+exec the right binary for the user's os/cpu.

### 1. Binary bundling — use PyInstaller (or Nuitka)

The whole point is "no Python," so PEX/shiv (which require a Python interpreter) are out.
PyInstaller produces a true standalone executable. Build targets:

| Platform | Runner |
|---|---|
| macOS arm64 | `macos-14` |
| macOS x64 | `macos-13` |
| Linux x64 | `ubuntu-latest` |
| Windows x64 | `windows-latest` |
| (later) Linux arm64 | QEMU or `ubuntu-24.04-arm` |

**⚠️ The hard part — sqlite-vec native extension.** Waystone loads the `sqlite_vec`
native library (`.so`/`.dylib`/`.dll`) at runtime. PyInstaller will NOT pick this up
automatically — it needs an explicit `--add-binary` / a hook that bundles the
`sqlite_vec` package's compiled extension, and the loader must find it inside the frozen
bundle (`sys._MEIPASS`). Budget real time here; this is the #1 thing that will break.
Cross-check with the existing Windows sqlite-vec cold-start work (Defender scans the
native binary on first load — a frozen exe may re-trip that).

Other bundling notes:
- `bge-small` embedding model (semantic extra) is large — keep it OUT of the default
  binary; semantic stays an opt-in `pip` path or a separate download. The MCP/CLI core
  binary should be lean.
- Entry points to expose: `waystone` (CLI) and `waystone-mcp` (MCP server).
- Smoke-test every binary in CI: `./waystone --version` and `./waystone selfcheck`.

### 2. Hosting

On each `vX.Y.Z` tag, a new CI job builds all binaries and uploads them to the GitHub
Release as assets (e.g. `waystone-0.4.29-darwin-arm64`, `...-win32-x64.exe`). Reuse the
existing tag-driven release flow alongside the PyPI publish.

### 3. npm packages — prefer the esbuild "optionalDependencies" pattern (no postinstall)

**Recommended (robust):** publish N platform packages + 1 launcher.
- `@waystone/darwin-arm64`, `@waystone/darwin-x64`, `@waystone/linux-x64`,
  `@waystone/win32-x64` — each contains just the matching binary, with `os`/`cpu` fields
  in its `package.json` so npm only installs the relevant one.
- `waystone` (the launcher) lists those as `optionalDependencies`; its `bin` is a tiny JS
  shim that locates the installed platform package and `execFileSync`s the binary,
  forwarding argv. No network call at install time → works in locked-down/CI installs.

**Simpler but more fragile (avoid if possible):** single `waystone` package whose
`postinstall` downloads the right binary from GitHub Releases. Easier to publish, but
`postinstall` network access is blocked in many sandboxes and is a security smell.

### 4. Versioning & release

- npm version stays in **lockstep** with the PyPI version — the same `vX.Y.Z` tag drives
  PyPI publish, binary builds, GitHub Release upload, and `npm publish`.
- Add an npm trusted-publish / `NODE_AUTH_TOKEN` step to the release workflow.
- Keep the platform packages' versions pinned exactly in the launcher.

### 5. MCP host config (the actual user-facing win)

```jsonc
// Claude Desktop / MCP host config
{
  "mcpServers": {
    "waystone": { "command": "npx", "args": ["-y", "waystone-mcp"] }
  }
}
```

## Risks / gotchas (in priority order)

1. **sqlite-vec native bundling** (above) — the make-or-break item.
2. **macOS Gatekeeper** — unsigned binaries get quarantined ("cannot be opened").
   Real distribution needs an Apple Developer ID cert + `codesign` + `notarytool`
   notarization in CI. Without it, mac users hit a scary dialog.
3. **Windows Defender / SmartScreen** — unsigned exes trip false positives (we already
   see Defender scanning the sqlite-vec native lib). Code-signing cert recommended.
4. **Binary size** — PyInstaller bundles of a SQLite+httpx+pyyaml app are tens of MB;
   acceptable, but keep semantic/torch OUT.
5. **Dual-channel maintenance** — every release now has to build/test/sign/publish 4+
   binaries plus npm. This is the ongoing cost; it's why this is demand-gated.

## Rough effort

- First working unsigned binary on one platform (incl. sqlite-vec hook): ~0.5–1 day.
- Full CI matrix + GitHub Release upload: ~0.5 day.
- npm launcher + platform packages + publish: ~0.5 day.
- macOS notarization + Windows signing (certs, CI secrets): ~1 day + cert procurement.
- **Total: ~3 days eng + code-signing cert acquisition lead time.**

## Definition of done

`npx -y waystone-mcp` starts the MCP server on a clean machine with no Python installed,
on macOS (signed/notarized), Windows (signed), and Linux; `npm install -g waystone` then
`waystone selfcheck` passes; npm version == PyPI version; release is one tag push.
