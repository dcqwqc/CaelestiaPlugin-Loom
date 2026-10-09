ChatGPT project move - live findings on 2026-10-09

UNVERIFIED FEATURE BRANCH; NOT DEPLOYMENT COMPLETE.

Verified: Zen bridge 0.10.13 loaded. One dedicated ChatGPT web worker was created with a canonical URL and expected exact response. Canonical URL recovery avoids resending. Empty Working and Done projects were resolved with message-free catalog window and their native project composer routes. Exact chat action control was found; pointerdown/pointerup opened actual Rename/Pin/Share/Archive/Delete/Move to project menu.

Not verified: moving a worker into Working and subsequently Done. Prior actor attempted a wrong sidebar option. This branch adds menu-scoped picker and controls, but further live verification is required.

Next steps: review pointer/move semantics, exercise picker on the already-created disposable chat without resending prompts, verify URL of same conversation after each project move, test approval transition, then independently review and deploy. Preserve active browser session; avoid unnecessary restarts.

Tests: python3 -m unittest discover -s tests; node --test tests/web_worker_actor.test.mjs
