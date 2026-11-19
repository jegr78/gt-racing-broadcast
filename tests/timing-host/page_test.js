// The public timing page's transport decisions (#1161). Run: deno test tests/timing-host/
const html = await Deno.readTextFile(new URL("../../src/console/timing.html", import.meta.url));
const block = html.match(/<script id="timing-logic">([\s\S]*?)<\/script>/);
if (!block) throw new Error("timing.html has no timing-logic script block");
const win = {};
new Function("window", block[1])(win);
const L = win.TimingLogic;

function eq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`);
}
const F = "https://relay.example.ts.net/console/timing?l=linkAAAAAAAAAAAAAAAAAAAA";
const base = { failedReconnects: 0, sinceDropS: 0, publisherUp: true, fallback: F };

Deno.test("3 failed reconnects and 12 s since the drop redirect", () => {
  eq(L.fallbackDecision({ ...base, failedReconnects: 2, sinceDropS: 60 }), "stay", "two");
  eq(L.fallbackDecision({ ...base, failedReconnects: 3, sinceDropS: 11 }), "stay", "too soon");
  eq(L.fallbackDecision({ ...base, failedReconnects: 3, sinceDropS: 12 }), "redirect", "three");
});

Deno.test("a silent open socket stays: the relay, not the host, went quiet", () => {
  eq(L.fallbackDecision({ ...base, sinceDropS: 600 }), "stay", "relay lost without a close: nothing but silence");
});

Deno.test("reconnects back off from 1 s to 8 s, never faster than 1 s", () => {
  const d = (n) => L.reconnectDelayMs({ failedReconnects: n, sinceDropS: 0 });
  eq([0, 1, 2, 3, 4, 5].map(d), [1000, 1000, 2000, 4000, 8000, 8000], "backoff");
});

Deno.test("a link that keeps refusing backs off to minutes, then gives up", () => {
  const s = { failedReconnects: 6, sinceDropS: 60 };
  eq(L.unavailable({ failedReconnects: 5, sinceDropS: 60 }), false, "five");
  eq(L.unavailable(s), true, "six: unavailable");
  eq(L.reconnectDelayMs(s), 120000, "minutes");
  eq(L.reconnectDelayMs({ ...s, sinceDropS: 1800 }), null, "stop after 30 min");
  eq(L.unavailable({ failedReconnects: 0, sinceDropS: 1800 }), true, "stopped is unavailable");
});

Deno.test("only https fallbacks count", () => {
  eq(L.fallbackUrl("https://f/x"), "https://f/x", "https");
  eq(L.fallbackUrl("javascript:alert(1)"), null, "script");
  eq(L.fallbackUrl(null), null, "cleared");
});

Deno.test("publisher down stays on the hosted page", () => {
  eq(L.fallbackDecision({ ...base, publisherUp: false, failedReconnects: 9, sinceDropS: 60 }), "stay", "relay restart");
});

Deno.test("null fallback stays", () => {
  eq(L.fallbackDecision({ ...base, fallback: null, failedReconnects: 9, sinceDropS: 60 }), "stay", "no Funnel");
});

Deno.test("redirect target says why the viewer left the host", () => {
  eq(L.fallbackTarget(F, "host"), F + "&from=host", "query appended");
  eq(L.fallbackTarget("https://x/console/timing", "host"), "https://x/console/timing?from=host", "no query yet");
  eq(L.fallbackTarget(F, "moved"), F + "&from=moved", "a deliberate move");
});

Deno.test("the Funnel page follows moved_to; a fallback viewer only after a dwell", () => {
  const H = "https://racecast-timing-demo.example.workers.dev/t/linkAAAAAAAAAAAAAAAAAAAA";
  eq(L.funnelMove({ moved_to: H }, "?l=x", 0), H, "moved");
  eq(L.funnelMove({ moved_to: H }, "?l=x&from=host", 30), null, "no ping-pong right after a fallback");
  eq(L.funnelMove({ moved_to: H }, "?l=x&from=host", 60), H, "the host is back: return");
  eq(L.funnelMove({ moved_to: H }, "?l=x&from=moved", 5), null, "the room may still hold the old move");
  eq(L.funnelMove({ moved_to: H }, "?l=x&from=moved", 10), H, "moved viewers return when hosted is served again");
  eq(L.funnelMove({}, "?l=x&from=moved", 600), null, "nothing to follow");
  eq(L.funnelMove({ moved_to: "javascript:alert(1)" }, "?l=x", 0), null, "https only");
});

Deno.test("the hosted path is recognised", () => {
  eq(L.hostedLink("/t/linkAAAAAAAAAAAAAAAAAAAA"), "linkAAAAAAAAAAAAAAAAAAAA", "hosted");
  eq(L.hostedLink("/console/timing"), null, "Funnel");
});

Deno.test("a brand logo resolves beside the page on both hosts", () => {
  const id = "linkAAAAAAAAAAAAAAAAAAAA";
  eq(L.brandUrl(`/t/${id}`, "", "bmw"), `/t/${id}/brand/bmw`, "hosted page");
  eq(L.brandUrl(`/t/${id}/`, "?from=x", "bmw"), `/t/${id}/brand/bmw`, "hosted page, trailing slash");
  eq(L.brandUrl("/console/timing", `?l=${id}&from=host`, "aston-martin"),
     `/console/timing/brand/aston-martin?l=${id}`, "Funnel page keeps only the link id");
  eq(L.brandUrl("/console/timing", `?from=moved&l=${id}`, "bmw"), `/console/timing/brand/bmw?l=${id}`, "any order");
  eq(L.brandUrl("/console/timing", "", "bmw"), "/console/timing/brand/bmw", "no id, no query");
  for (const bad of ["", "BMW", "../x", "a/b", null]) eq(L.brandUrl("/console/timing", "", bad), null, `no URL for ${bad}`);
});

Deno.test("a missing logo is asked for again later and later", () => {
  const M = L.LOGO_RETRY_MS;
  eq(L.logoWanted(undefined, 0), true, "never failed");
  eq(L.logoWanted({ at: 1000, n: 1 }, 1000 + M - 1), false, "failed just now");
  eq(L.logoWanted({ at: 1000, n: 1 }, 1000 + M), true, "a minute later, the host may have it now");
  eq(L.logoWanted({ at: 0, n: 3 }, 4 * M - 1), false, "the third failure waits four minutes");
  eq(L.logoWanted({ at: 0, n: 3 }, 4 * M), true, "then asks again");
  eq(L.logoWanted({ at: 0, n: 40 }, L.LOGO_RETRY_MAX_MS), true, "never longer than the ceiling");
  eq(L.logoWanted({ at: 0, n: 40 }, L.LOGO_RETRY_MAX_MS - 1), false, "but up to it");
});

Deno.test("a logo from the host's socket becomes bytes for that key only when it is a PNG", () => {
  const ok = btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2));
  const got = L.brandMessage({ t: "brand", key: "bmw", png: ok });
  eq(got && got.key, "bmw", "key");
  eq(got && Array.from(got.bytes).slice(0, 4), [0x89, 0x50, 0x4e, 0x47], "decoded bytes");
  let big = String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a);
  big += "\0".repeat(256 * 1024);
  for (const bad of [{ t: "brand", key: "../x", png: ok }, { t: "brand", key: "bmw", png: btoa("<svg onload=alert(1)></svg>") },
                     { t: "brand", key: "bmw", png: "%%%" }, { t: "brand", key: "bmw" },
                     { t: "brand", key: "bmw", png: btoa(big) }]) {
    eq(L.brandMessage(bad), null, JSON.stringify(bad).slice(0, 60));
  }
});
