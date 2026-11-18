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
const base = { failedReconnects: 0, publisherUp: true, fallback: F };

Deno.test("3 failed reconnects with a fallback redirects", () => {
  eq(L.fallbackDecision({ ...base, failedReconnects: 2 }), "stay", "two");
  eq(L.fallbackDecision({ ...base, failedReconnects: 3 }), "redirect", "three");
});

Deno.test("a silent open socket stays: the relay, not the host, went quiet", () => {
  eq(L.fallbackDecision({ ...base }), "stay", "relay lost without a close: nothing but silence");
});

Deno.test("only https fallbacks count", () => {
  eq(L.fallbackUrl("https://f/x"), "https://f/x", "https");
  eq(L.fallbackUrl("javascript:alert(1)"), null, "script");
  eq(L.fallbackUrl(null), null, "cleared");
});

Deno.test("publisher down stays on the hosted page", () => {
  eq(L.fallbackDecision({ ...base, publisherUp: false, failedReconnects: 9 }), "stay", "relay restart");
});

Deno.test("null fallback stays", () => {
  eq(L.fallbackDecision({ ...base, fallback: null, failedReconnects: 9 }), "stay", "no Funnel");
});

Deno.test("redirect target marks the visit from the host", () => {
  eq(L.fallbackTarget(F), F + "&from=host", "query appended");
  eq(L.fallbackTarget("https://x/console/timing"), "https://x/console/timing?from=host", "no query yet");
});

Deno.test("the Funnel page follows moved_to unless the viewer came from the host", () => {
  const H = "https://racecast-timing-demo.example.workers.dev/t/linkAAAAAAAAAAAAAAAAAAAA";
  eq(L.funnelMove({ moved_to: H }, "?l=x"), H, "moved");
  eq(L.funnelMove({ moved_to: H }, "?l=x&from=host"), null, "no ping-pong");
  eq(L.funnelMove({}, "?l=x"), null, "nothing to follow");
  eq(L.funnelMove({ moved_to: "javascript:alert(1)" }, "?l=x"), null, "https only");
});

Deno.test("the hosted path is recognised", () => {
  eq(L.hostedLink("/t/linkAAAAAAAAAAAAAAAAAAAA"), "linkAAAAAAAAAAAAAAAAAAAA", "hosted");
  eq(L.hostedLink("/console/timing"), null, "Funnel");
});
