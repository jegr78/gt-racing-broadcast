# The Console launcher

> Run the read-only desk? See the visual [Race Control onboarding deck ↗](https://jegr78.github.io/gt-racing-broadcast/race-control.html). All crew decks: the [onboarding decks ↗](https://jegr78.github.io/gt-racing-broadcast/).

`/console` is the **single page** every crew member opens. There are no separate URLs per
surface: one page adapts to the signed-in person's role and shows only the cards they are
allowed to use: nothing more.

![The /console launcher, role-adaptive cards: Commentator Cockpit, Race Control, Director Panel, Web Buttons, and the universal Health Monitor](images/console-landing.png)

## How it works

The producer shares **one Console link** (the same `/console` URL for everyone). Opening
it shows a **Login with Discord** button; after signing in, the relay matches the person's
Discord handle to the league's crew roster (the Crew tab ∪ the live schedule) and renders
their role's cards. Roles are resolved live on every request, so adding someone, or
changing their role on the roster: takes effect immediately, with no link to re-send.

> **Fallback: personal sign-in links.** A league that hasn't set up Discord login can
> instead hand out per-person signed links (`racecast links`): opening one signs that
> person in directly, no Discord needed. Setting up Discord login is the
> [League-Owner Setup](League-Owner-Setup) job; issuing/revoking the fallback links is in
> [Console & cockpit setup](Console-Setup#personal-sign-in-links-fallback-issue--revoke).

Either way the page works **over the tailnet** (e.g. a phone with the Tailscale app) **or
over the public Funnel** (`racecast funnel on`, no Tailscale account needed on the crew
member's side). See [Remote access & the Funnel boundary](Remote-access) for the full
security model.

## The cards

| Card | Path | Who sees it |
|---|---|---|
| **Commentator Cockpit** | `/console/cockpit` | commentators (on the schedule or flagged **Commentator**) |
| **Race Control** | `/console/race-control` | crew flagged **Race Control**, and every **producer** |
| **Director Panel** | `/console/panel` | directors: and every **producer** |
| **Web Buttons** | `/console/buttons` | directors / producers (requires Companion ≥ v4.1.0) |
| **Health Monitor** | `/console/health-monitor` | any authenticated person |

A **producer oversees the whole event**, so the **Producer** crew flag automatically
grants the **Director** and **Race Control** roles too: a producer always sees the
Director Panel, Web Buttons and Race Control cards and can both monitor and steer the
broadcast: without a separate Director or Race Control flag. (The producer-only
broadcast-control operations: stint takeover, race/qualifying mode, still require the
shared producer step-up secret.)

Each card leads to the same page as its tailnet equivalent: `/cockpit`, `/panel`, and
the Companion Web Buttons board at `:8000/tablet` respectively, but reached through
the role-gated `/console` mirror, with API calls transparently routed to the correct
endpoints.

### Race Control (read-only monitoring desk)

**Race Control** is a monitoring desk: a live **program preview**, the **streamer / stint
schedule**, the **race timer**, and **crew chat**. *Read-only* means it triggers **no
broadcast actions**, no scenes, graphics or feeds; the **director keeps full control of
the Panel**. The **crew chat is two-way**, though, and is the desk's working channel: the
operator is expected to **post race-control information** for the crew (e.g. a
drive-through / DSQ for a team, a car's rejoin time, a team that can't field a driver for
the next stint, a team retiring from the race) and to **direct the commentators** through
it (e.g. "cut to car #7, P3"). Messages post under the operator's own name. Flag a person
for it with the **Race Control** column on the Sheet's Crew tab (or the Control Center crew
editor); the role string is `race_control`. The schedule it shows is **redacted**: stream
URLs never leave the tailnet, the same boundary as the producer-takeover status, so the
desk is safe over the public Funnel.

![Race Control desk: program preview, redacted streamer/stint schedule with an on-air marker, race timer and crew chat](images/console-race-control.png)

> **Naming note:** the role shares its label with the director-only HUD **Race Control**
> banner (the Setup-tab `Race Control` field shown on the lower third). They are
> unrelated: the role is `race_control` (Crew tab), the banner is `racecontrol` (Setup
> tab). This role never writes to that banner.

Every card opens **in the same tab**. Every destination carries a **`← Console`**
back link in its header that returns to this launcher, so there is clean
forward-and-back navigation without relying on the browser history. The Web Buttons card
opens a thin `/console/buttons` wrapper that embeds the Companion board in an iframe and
hosts that back link (Companion's own page can't carry it); the buttons themselves are
unchanged. The back link is shown only under the `/console` mount: at the tailnet
`/cockpit` and `/panel` URLs there is no launcher to return to, so it stays hidden.

A person can hold multiple roles (e.g. a commentator who is also a director); all
their cards appear on one landing page. Roles are resolved live from the Crew tab and
the active schedule on every request, so a role change takes effect immediately without
re-issuing the link.

## Further reading

- [League-Owner Setup](League-Owner-Setup): how to configure Discord OAuth, register
  redirect URIs, and maintain the Crew tab so crew members can log in with Discord.
- [Remote access & the Funnel boundary](Remote-access): security model, the Funnel
  mount, and how roles are authorised.
- [Commentator Cockpit](Commentator-Cockpit), the commentator-facing cockpit in detail.
- [Director guide](Director): the full Director Panel reference.
- [Companion (button config)](Companion): the Web Buttons board and how to configure
  Companion buttons.

---

> This page is generated from `src/docs/wiki/` in the
> [main repository](https://github.com/jegr78/gt-racing-broadcast): don't edit it
> here by hand. See [Build & maintenance](Build-and-maintenance).
