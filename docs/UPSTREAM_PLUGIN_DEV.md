# Native Caelestia plugin development migration — CUSTOM SHELL PANEL BLOCKER

Target: https://github.com/caelestia-dots/shell/pull/1703 (unmerged).
Feature branch: `feat/upstream-plugin-dev-20261021`. The original default branch and this branch's working legacy `manifest.json` are deliberately preserved.

## Audit
- Plugin: `dcqwqc/Loom`, current version 0.2.1.
- Unsupported entries on upstream: `Panel.qml` (`shell-panel`, {"edge":"top","alignment":"center","interactive":true,"deformAmount":0.025,"motionDuration":180}).
- Other native entry points preserved: custom: Main.qml.
- `manifest.native-dev.json` is a **preview only**, with the unsupported entries removed and the >=2.1.0 version requirement added. It is not automatically loaded: the dev loader scans for `manifest.json`. To test in a disposable checkout copy this preview over the manifest there, leaving the original branch and installations untouched.

## Blocker: native shell panel host
The upstream development parser accepts custom, bar-entry, bar-popout, status-icon, quick-toggle and dashboard-tab; **it does not accept shell-panel**. A `custom` entry does not reproduce the shell-owned screen-aware panel window, deformation, hover/input masking, and position. Blindly renaming the entry would make a feature silently disappear. Implement an explicit adapter when upstream exposes an equivalent host, or maintain a small compatibility extension in the shell. Preserve existing panel QML and backend code.

## Predeployment gates
1. In an isolated upstream #1703 build, test all manifest sources, QML import modules, and enabled/disabled/reloaded state.
2. Restore the native panel equivalent and verify its geometry, input, multi-monitor and fullscreen behavior before enabling the branch for a real user.
3. Verify settings/data persistence, old-backend service behavior, boot/restart recovery.
4. Keep backups and auto-rollback to the legacy installation on any regression.

**State:** Preview manifest generated but legacy root manifest retained. Do not deploy this branch as a fully native plugin yet.
