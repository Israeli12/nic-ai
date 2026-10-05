# Safety model

The assistant can lock your laptop, dial your phone, and move your files.
Three mechanisms keep that in check.

## 1. Confirmation on dangerous actions

Tools marked dangerous never run unattended. In the CLI you get a
`[confirm]` prompt; in the web UI an Allow/Cancel panel; with no one
present, the action is refused rather than assumed. Currently dangerous:
`laptop_lock`, `laptop_power`, `laptop_run_command`, `android_call`,
`android_lock`.

Turn confirmations off only if you accept the consequences:

```yaml
safety:
  confirm_dangerous_actions: false
```

## 2. Blocklist and allowlists

```yaml
safety:
  blocked_tools: [android_call, laptop_power]   # removed entirely
laptop:
  allow_shell: false                            # no arbitrary commands
  app_allowlist: [notepad, chrome]              # only these launch
```

`blocked_tools` is applied twice: the tool is dropped from the registry so
the model never sees it, and the guard refuses it even if asked by name.

## 3. Audit log

Every tool call, its arguments, and its outcome (`ok`, `denied`, `error`)
is appended as JSON to `safety.audit_log`, by default
`~/nic-ai/audit.log`. Read it with:

```powershell
Get-Content $HOME\nic-ai\audit.log -Tail 20
```

## Web UI exposure

`python -m nic serve` binds `0.0.0.0` so your phone can reach it, which
means everything on your Wi-Fi can reach the port too. A token is
mandatory - the server refuses to start without one. It is plain HTTP on
your LAN, so do not run it on a network you do not trust, and do not port
forward it to the internet. If you want laptop-only access, set
`web.host: 127.0.0.1`.

## Shell access

`laptop.allow_shell: true` gives a 4B model the ability to run any command
as you. It is off by default for good reason. If you enable it, keep
`confirm_dangerous_actions: true` so every command needs your yes.
