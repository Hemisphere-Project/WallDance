# WallDance Copilot Instructions

## Terminal Execution

- Use background terminals only for long-running processes such as dev servers, watchers, or continuous builds.
- Do not start short-lived commands in background terminals when foreground execution is sufficient.
- When multiple short commands depend on each other, combine them into a single shell invocation instead of launching separate background jobs.
- Prefer a single command that writes structured output to a file, then read the file with tools, instead of polling terminal output.
- When waiting on a background process, check for a concrete readiness signal in output or a health check condition instead of relying on fixed long sleeps.
- For processes expected to become ready quickly, poll early with short intervals before backing off.
- After starting a long-running background process, continue follow-up work in foreground terminals whenever possible.
- Avoid serial patterns like "start background command, sleep for many seconds, inspect output, then start the next short command" when the sequence can be done in one shell command or one script.

## Workflow Preference

- Prefer scripts or task wrappers for repeated multi-step flows so the shell handles sequencing instead of the agent.
- If a command will finish quickly and its output is needed immediately, run it directly rather than through a background terminal.