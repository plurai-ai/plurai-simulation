# Backend check block — canonical source

Every skill that reaches the backend and is not one of the seven flow skills carries a generated copy of the block
below, between `<!-- BEGIN backend-check … -->` and `<!-- END backend-check -->` markers, as its first section. The
flow skills run the same check inside their Step 0 block (`docs/step0-block.md`), after their readiness and login
steps. The block runs before the skill looks at its tool list: a refused plugin has no backend tools, and the check
needs none.

The copy is the same in every skill, so it says `the command the user typed` and never a skill's own name. The
error-posture block (`docs/error-posture.md`) holds the rules the block points at.

<!-- BEGIN CANONICAL BLOCK -->
## Check the backend first

Before anything else in this skill, run the backend check. It needs no backend tool and no SDK, and it comes before the
tool-list step of the error posture:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"
```

- Exit 0: go on. A line it printed is an update notice: say it once, as printed, first; it stops nothing.
- Exit 7 is a version refusal: follow the error posture's version refusal rule. Say what the check printed, verbatim and
  alone, with nothing before it: not the exit code, not that it is a refusal. Then stop. No tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line (no usable login; this skill has no readiness step, so the check is its login
  test): run the `harness-setup` skill from the command the user typed (`from /<command>`) and show the user nothing from
  the line. That skill checks the backend after its login, so go on when it reports Plurai set up, and stop when it stops.
  Any other line, such as a `NOT READY` line for hooks Codex has not trusted: tell the user in plain words what it says
  to fix, and stop.
<!-- END CANONICAL BLOCK -->
