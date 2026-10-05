# Android control

Android allows genuine device control offline, over ADB. A USB cable or
your own Wi-Fi is all it uses.

## One-time phone setup

1. **Settings -> About phone**, tap **Build number** seven times to unlock
   Developer options.
2. **Settings -> System -> Developer options**, enable **USB debugging**
   (and **Wireless debugging** if you want cable-free use).
3. Plug the phone into the laptop and accept the *Allow USB debugging?*
   prompt. Tick "Always allow from this computer".

## One-time laptop setup

1. Download Android **platform-tools** from
   <https://developer.android.com/tools/releases/platform-tools>.
2. Unzip it, e.g. to `C:\platform-tools`.
3. Either add that folder to PATH, or set it in `config.yaml`:

```yaml
android:
  adb_path: C:\platform-tools\adb.exe
```

Verify:

```powershell
adb devices          # should list your phone as "device", not "unauthorized"
python -m nic doctor
```

## Wireless (no cable)

With the phone on the same Wi-Fi, in **Developer options -> Wireless
debugging**, use *Pair device with pairing code*:

```powershell
adb pair 192.168.1.50:37123     # address and code shown on the phone
adb connect 192.168.1.50:5555
```

Re-pairing is usually needed after a phone reboot.

## What you can ask for

| Ask | Tool used |
| --- | --- |
| "How's my phone battery?" | `android_status` |
| "Open Spotify on my phone" | `android_open_app` |
| "Screenshot my phone" | `android_screenshot` |
| "What notifications do I have?" | `android_notifications` |
| "Text Mum that I'm running late" | `android_compose_sms` (you press send) |
| "Call +256..." | `android_call` (asks you first) |
| "Pull that photo to my laptop" | `android_pull_file` |
| "Lock my phone" | `android_lock` (asks you first) |

Taps and swipes (`android_tap`, `android_swipe`) exist for driving an app's
UI, but coordinates are screen-specific: take a screenshot first so the
model can see where things are.

## Limitations worth knowing

- Sending an SMS silently is not possible without installing a helper app
  on the phone; nic-ai opens the composer pre-filled instead.
- Some manufacturers (Xiaomi, Huawei) restrict `monkey` app launching;
  if "open app" fails, the fallback is `android_key` plus taps.
- ADB cannot act while the phone is encrypted-and-rebooted (before the
  first unlock).
