# Other commands

Beyond the lifecycle, these commands are available when you need them:

| Command | What it does |
| --- | --- |
| `/analyze-simulation` | After a run, do a deeper quality analysis of the whole simulation and iterate to improve it. |
| `/refine-scenarios` | Interactively steer *which* scenarios get generated (an alternative to the generation inside `/simulation-generate-suite`). |
| `/build-rag-corpus` | Build a RAG/SQL knowledge corpus as ground truth for an agent with retrieval tools. |
| `/edit-env` | Open a session with the backend to edit a previously-generated mock environment in place. |
| `/upload-doc-examples` | Upload real example documents so generated documents template from real formatting. |
| `/ground-scenarios` | Ground scenarios in the agent's real material — routes conversations-to-continue (trace exports) and real records to the right command, then hands off to scenario generation. |
| `/record-regression` | Record one confirmed agent issue as a regression — a folder that replays the conversation which exposed it, so the next suite build keeps a scenario reproducing it. `/simulation-fixes` runs it on acceptance; running it by hand works the same. |
| `/author-trace-adapter` | Diagnose a trace export the converter does not already understand, and author the workspace adapter that teaches it the format. |
| `/trace-to-scenarios` | The command `/ground-scenarios` uses for conversations; you can also run it directly. Turns thread exports into scenarios that RESUME where the real conversation stopped. |
| `/upload-scenario-bundle` | The command `/ground-scenarios` uses for records; you can also run it directly. Publishes hand-authored folders of real records so generated scenarios are grounded in them. |
| `/harness-setup` | Log in again, or switch organization. |
| `/optimize-eval` | Improve one eval's judge and compare the candidates. You choose whether to adopt one. |
| `/plurai-diagnose` | Print a bundle to send to Plurai support. |
| `/plurai-uninstall` | Remove what the plugin created in your project and home folder, after you confirm. |
