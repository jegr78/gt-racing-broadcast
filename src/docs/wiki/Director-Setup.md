# Director setup

Get ready to direct: usually **nothing to install**. You direct from a normal
browser, you never touch the producer's PC, and you need **no OBS IP, port, or
password**: the relay drives the producer's local OBS on your behalf.

## What you need

- A device with a browser: tablet, laptop, or phone.
- The producer's **Console link** (the shared `/console` URL).
- A **Discord account** that the producer has added to the league's crew roster.

You sign in to the Console with **Discord**: there's no per-person link to keep track
of. The producer adds your Discord handle to the crew roster and marks you as a
**director**; signing in with that account is what gives you the director controls. Roles
are looked up live, so a change applies the moment you reload, no new link to re-send.

## Step 1: Open the Console link and sign in

Open the producer's Console link in any browser. If you're not signed in yet it shows a
**Login with Discord** button: tap it, authorize, and you land back on **`/console`**, a
single page that shows only the surfaces your role allows. As a director you get a
**Director Panel** card (the whole show on one page) and, if the producer runs
Companion ≥ v4.1.0, a **Web Buttons** card (the big-button board). Tap a card to go
straight in.

It works **over the public internet** when the producer has the Funnel on, **no
Tailscale account, nothing to install**. After the first sign-in your session lives in a
secure cookie, so you can just reopen the page.

**Bookmark it** (or **Add to Home Screen** on a tablet) so you can reopen it in one
tap on event day.

> **No Discord login at your league?** Some leagues don't set it up, then the producer
> sends you a **personal sign-in link** instead (generated with `racecast links`, or
> copied from the Control Center's **Crew Console** view). Open it once; it signs you in
> the same way and everything below works identically.

> See the [Console launcher](Console) for a card-by-card tour, and the
> [Director guide](Director) for what the panel and buttons do.

## The alternative: on the tailnet (Tailscale)

Being **directly on the tailnet** is the alternative, mainly for the producer's own
trusted devices or when the Funnel isn't turned on. If the producer asks you to join
their Tailscale network:

1. **Install Tailscale.**

   | Your device | Where to get it |
   |---|---|
   | iPad / iPhone | App Store → "Tailscale" |
   | Android | Play Store → "Tailscale" |
   | Windows / macOS | <https://tailscale.com/download> |
   | Linux | your distribution's package, or <https://tailscale.com/download/linux> |

   **Linux has no Tailscale app**, it runs as a background service. Sign in once
   with `sudo tailscale up` (it prints a `https://login.tailscale.com/…` URL to open
   in a browser), then `sudo tailscale set --operator=$USER` so you can connect later
   without `sudo`. Check it with `tailscale status`.

2. **Accept the producer's invite** and sign in with your own account. You're ready
   when the app shows **Connected** (Linux: `tailscale status` reports your `100.x.y.z`
   address).

3. **Open the same `/console` link**, it works over the tailnet too, or, if the
   producer gave you the tailnet addresses directly, the panel at
   `http://<producer-tailscale-ip>:8088/panel` and the Web Buttons at
   `http://<producer-tailscale-ip>:8000/tablet`.

> **What is Tailscale?** A private-network app that makes *only* the producer's
> machine reachable from your device. The tailnet addresses (`100.x.y.z`) won't load
> without it.

## If you cannot connect

Things you can check yourself, in order:

| Check | Fix |
|---|---|
| Console shows **not signed in** | Tap **Login with Discord** and authorize, and make sure it's the **Discord account the producer added to the crew roster**, not a personal alt. |
| Signed in, but no **Director Panel** card | Your account isn't marked as a director on the roster yet. Ask the producer to set your **Director** flag for your handle, it applies immediately, no new link. |
| No **Login with Discord** button at all | Your league may not use Discord login. Ask the producer for your **personal sign-in link** instead. |
| Funnel link won't load at all | The producer needs the Funnel on (`racecast funnel on`). Ask them to turn it on, or fall back to the tailnet path above. |
| Page loads, but a red **RELAY UNREACHABLE** banner shows | You ARE connected: the problem is on the producer's side. Tell the producer; `racecast status` shows them what's down. |
| Panel loads, but **OBS NOT REACHABLE** / scene buttons grey | The relay can't reach OBS on the producer's machine, nothing you set up. Tell the producer. (You never enter an OBS password; the relay holds it.) |
| On the tailnet and nothing loads | Open the Tailscale app and check it's **Connected**, signed into the invited account, and you're using the producer's `100.x.y.z` address, not `localhost` or a `192.168.…` one. |
| Still stuck | Ask the producer to run `racecast status` (and `racecast tailscale status` for the tailnet path): those name the problem. |

---

Connected? → the [Director guide](Director) shows what the buttons do. The full
security model is in [Remote access & the Funnel boundary](Remote-access).
