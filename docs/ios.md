# iPhone bridge (limited by design)

Be clear-eyed about this one: **iOS has no offline device-control API**.
There is no iPhone equivalent of ADB. What is possible is running
*Shortcuts* that you created yourself, triggered from your laptop over
your own Wi-Fi. So the iPhone side is "ask the phone to run something it
already knows how to do", not "control the phone".

What works: running a named Shortcut, reading battery/connectivity if you
build a Shortcut that reports it, showing a notification, and anything else
you can express as a Shortcut (play music, set a timer, send a message
with a confirm tap, toggle a HomeKit device).

What does not work offline, at all: taps, screenshots, reading arbitrary
app state, silent messaging, or installing anything remotely.

## Setting up the bridge

1. On the iPhone, install **Shortcuts** (preinstalled) and the free
   **Pushcut** app, or any app that can host a local HTTP endpoint.
2. Create a Shortcut named `nic-bridge` that:
   - takes the request body as input,
   - gets the `action` and `secret` values from it,
   - stops if `secret` does not match the one you choose,
   - runs the Shortcut named in the request, or shows a notification.
3. Note the local URL the hosting app gives you, e.g.
   `http://192.168.1.42:7878/nic-bridge`.
4. In `config.yaml`:

```yaml
ios:
  enabled: true
  bridge_url: http://192.168.1.42:7878/nic-bridge
  shared_secret: pick-something-long
```

Then `python -m nic doctor` will confirm the bridge is configured, and the
`ios_run_shortcut`, `ios_status`, and `ios_notify` tools become available.

## Honest recommendation

If controlling the phone matters to you, do it from the Android handset
and treat the iPhone as a notification target. Keep `ios.enabled: false`
until you have the Shortcut working - the tools will simply be absent, and
the assistant will say so rather than inventing capability.
