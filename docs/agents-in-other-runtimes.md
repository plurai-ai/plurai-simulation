# Agents in other runtimes

An agent whose code is not Python keeps running in its own runtime: the generated `simulate.py`
drives it through a **bridge** child process, so the mocks and evals are the same as for a
Python agent. Node (TypeScript or JavaScript) is the bridge child that ships, and it is the default
for every Node agent the bridge's preflight accepts — Mastra, Vercel AI SDK, LangGraph.js, OpenAI
Agents JS or a custom loop. Refused by name: Bun and Deno, a Windows host, a Yarn Plug'n'Play
install, a `worker_threads` worker a turn starts (refused at run time), and a raw-SQL driver (`pg`, `mysql2`, …) with no
data-access module of the agent's own to map. It needs two runtimes: Python with the plurai SDK venv
**and** Node ≥ 22.15. The bridge installs its own pinned runtime privately (`npm ci` into
`plurai_sim/bridge/runtime/`) and never touches your `package.json`; npm's cache under `~/.npm` is
its one write outside `plurai_sim/`. Any other runtime (Go, Java, Ruby, …) is refused by name, never
improvised.
