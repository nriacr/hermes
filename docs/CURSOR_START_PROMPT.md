# Cursor First-Chat Prompt

Copy the block below into the first Cursor Agent chat opened for this repository.

```text
You are continuing development of Hermes, a production Home Assistant add-on in
the currently opened repository.

Before doing any work:
1. Read AGENTS.md completely.
2. Read docs/CURSOR_HANDOFF.md completely.
3. Inspect git status, current branch, latest commits, and the actual version in
   ha-addon/config.yaml.
4. Treat current source code and tests as authoritative; do not rely on old chat
   history or assumptions.

Working agreement:
- Communicate with me in clear Turkish; I am not a programmer.
- For clear tasks, inspect, implement, test, version, commit, and push without
  making me perform coding steps.
- Do not use temporary patches or duplicate implementations. Fix root causes and
  remove superseded code.
- Every commerce site is an isolated provider. A site-specific fix must not alter
  any other site's price-reading behavior.
- Preserve Home Assistant options, ports, routes, /data state, notifications,
  min/max history, and ingress/public/mobile parity unless I approve migration.
- Add regression tests for parser/business bugs. Run focused tests while working
  and sh tools/check.sh before release.
- Read the current version before bumping it. Runtime changes require a patch
  bump; documentation-only changes do not.
- Never commit secrets, tokens, Telegram sessions, runtime state, or captured
  authenticated pages.
- Completed runtime work must be committed and pushed to main so Home Assistant
  receives the update.

At the start, give me only a short confirmation containing:
- the current version and commit;
- whether the worktree is clean;
- your one-paragraph understanding of Hermes architecture;
- any immediate blocking issue.

Then wait for my first development request.
```
