# Putting nic on your phone's home screen

The web UI is a progressive web app, so it installs to the home screen
with its own icon and opens full-screen, with no browser chrome.

## 1. Start the server on the laptop

```powershell
python -m nic serve
```

It prints the address to use, e.g. `http://192.168.1.20:8713`.
`python -m nic qr` prints the same address as a scannable QR code
(`pip install qrcode`), which saves typing it on the phone.

## 2. Android (Chrome)

1. Open that address in Chrome.
2. Paste your `web.access_token` once - it is remembered on the device.
3. nic offers *"Add nic to your home screen?"* - tap **Add**.
   (If you dismissed it: menu **⋮ -> Add to Home screen**.)

You get a round `nic` icon on the home tab that opens straight into the
chat, no address bar.

## 3. iPhone (Safari)

Safari does not show an install prompt, so do it by hand:

1. Open the address in **Safari** (not Chrome - only Safari can install).
2. Tap **Share** -> **Add to Home Screen** -> **Add**.

The icon and full-screen behaviour come from the `apple-touch-icon` and
`apple-mobile-web-app-capable` tags, which are already in the page.

## 4. Talking to it from the phone

The **Talk** button in the header uses the phone's own speech
recognition, so you can speak instead of typing. That recogniser belongs
to the phone, not to nic-ai - on most Android builds it goes through
Google unless you have downloaded offline recognition. The fully offline
voice path is `python -m nic wake` on the laptop. If that distinction
matters to you, type on the phone and speak at the laptop.

## Things worth knowing

- **It needs the laptop awake.** The phone app is a remote control; the
  model, the tools, and your files all live on the laptop. Out of Wi-Fi
  range, it will not connect.
- **The laptop's IP can change.** If the shortcut stops working, check
  `python -m nic qr` for the current address. A DHCP reservation in your
  router makes it permanent.
- **The offline cache needs HTTPS.** Browsers only register a service
  worker in a secure context, so over plain LAN http the shell is
  re-fetched each launch. The shortcut itself works regardless.
- **Approvals still apply.** Dangerous actions show an Allow/Cancel panel
  in the app, exactly like the terminal prompt.
