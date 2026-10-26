# dsh-whale-bridge

Read-only DSH Host plugin that projects the running agent's coarse work state for the Big-Fat-Fish desktop pet.

- **Status file** (pet polls this): `<DSH_HOME or ~/.dsh>/whale-bridge/status.json`, rewritten atomically on every state change and every 5 s as a heartbeat.
- **Loopback route** (verification): `GET /api/whale/status` on the running DSH web server (127.0.0.1, OS-assigned port — never hardcode it).

Public state (the only data that ever leaves the plugin):

```json
{"ok":true,"state":"tool","lastTool":"pwsh","doneCount":3,"errorCount":1,"approvalOpen":false,"updatedAt":1792942038743,"version":1}
```

`state` ∈ `idle | running | tool | success | failure | approval`. No prompts, tool arguments, tool output, file paths, todo text or command arguments are ever exposed.

- Install / verify / rollback: see `../INSTALL.md` (desktop profile, DSH Desktop 0.2.1-rc.3).
- Design, privacy contract, actual test output: see `../README.md`.
- Bundle layer: `cordis.patch.yml` (inserts one Host row; `inject: ["webServer"]`).
- Optional config (profile `cordis.patch.yml` override): `statusFile` (absolute path), `heartbeatMs` (default 5000).

License: MIT.
