# Simulation Environments

Once you've set up a mock env + scenarios + evals, you can snapshot the whole
thing as a **named, committable environment** and switch between several (e.g.
one per component under test):

These are **natural-language operations, not slash commands** — just tell Claude
what you want and it drives the env manager for you:

| Say to Claude | What happens |
| --- | --- |
| _"save this as env `billing-happy`"_ | save the current setup into `plurai_sim/envs/billing-happy/` |
| _"save this as env `refunds-edge`"_ | a second environment |
| _"switch to env `billing-happy`"_ | switch to that env and re-activate its mock, with no regeneration |
| _"list envs"_ | see all envs + backend status (deployed? sets present? drifted?) |

Each environment holds its inputs (`plurai.config.yaml`, `simulation_prd.md` and others) and a `plurai.env.yaml` manifest
that records what it uses on the backend. Switching re-activates its mock without regenerating it, and warns (but doesn't
block) if an input changed since the environment was generated.

**Commit** `plurai_sim/envs/**/plurai.env.yaml`, the generated artifacts and the inputs you wrote. The plugin adds a block
to your repository's `.gitignore` that keeps machine-local files and your extracted data out of git.
