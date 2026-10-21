# Native Caelestia plugin development migration — CUSTOM SHELL PANEL BLOCKER

Target: https://github.com/caelestia-dots/shell/pull/1703 (unmerged).
Feature branch: `feat/upstream-plugin-dev-20261021`. The original default branch is preserved and the original `shell-panel` manifest is saved as `manifest.legacy.json` in this branch.

## Audit
- Plugin: `dcqwqc/Loom`, current version 0.2.1.
- Unsupported entries on upstream: `Panel.qml` (`shell-panel`, {"edge":"top","alignment":"center","interactive":true,"deformAmount":0.025,"motionDuration":180}).
- Other native entry points preserved: custom: Main.qml.
- `manifest.json` is now the upstream-compatible development manifest; `manifest.native-dev.json` is the identical review snapshot. `manifest.legacy.json` preserves the previous panel declarations, and can be copied back in a disposable test checkout when restoring private shell-panel support.

## Blocker: native shell panel host
The upstream development parser accepts custom, bar-entry, bar-popout, status-icon, quick-toggle and dashboard-tab; **it does not accept shell-panel**. A `custom` entry does not reproduce the shell-owned screen-aware panel window, deformation, hover/input masking, and position. Blindly renaming the entry would make a feature silently disappear. Implement an explicit adapter when upstream exposes an equivalent host, or maintain a small compatibility extension in the shell. Preserve existing panel QML and backend code.

## Predeployment gates
1. In an isolated upstream #1703 build, test all manifest sources, QML import modules, and enabled/disabled/reloaded state.
2. Restore the native panel equivalent and verify its geometry, input, multi-monitor and fullscreen behavior before enabling the branch for a real user.
3. Verify settings/data persistence, old-backend service behavior, boot/restart recovery.
4. Keep backups and auto-rollback to the legacy installation on any regression.

**State:** Native-loader-compatible root manifest staged; existing native shell-panel visuals still require a real adapter. The default branch and previous manifest are preserved. Do not deploy this branch as fully functional yet.

## Production-safe merge profile

The root `manifest.json` retains the deployed Caelestia fork entry points and has **no upstream-only minimum version requirement**. The experimental official API profile is in `manifest.native-dev.json`; it is not used by the current fork loader. Do not replace the root file with the development profile until the upstream host implements all required custom panels and placement hooks.
