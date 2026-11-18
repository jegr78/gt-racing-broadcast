// The timing host Worker and its Durable Object room (#1161). Run: deno test tests/timing-host/
import worker, { setPage, TimingRoom } from "../../src/timing-host/worker.js";

const LINK = "linkAAAAAAAAAAAAAAAAAAAA";
const KEY = "publish-key-1-long-enough";
const MIN = 60 * 1000;
const DAY = 24 * 60 * MIN;

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}
function eq(a, b, msg) {
  const ja = JSON.stringify(a), jb = JSON.stringify(b);
  if (ja !== jb) throw new Error(`${msg}: ${ja} !== ${jb}`);
}

// Workers runtime stand-ins: Response that allows 101, WebSocketPair, DO state.
class FakeResponse {
  constructor(body, init = {}) {
    this.body = body ?? "";
    this.status = init.status ?? 200;
    this.headers = new Headers(init.headers || {});
    this.webSocket = init.webSocket;
  }
  text() {
    return Promise.resolve(String(this.body));
  }
}
globalThis.Response = FakeResponse;

class FakeSocket {
  constructor() {
    this.sent = [];
    this.closed = null;
  }
  send(m) {
    if (this.closed) throw new Error("closed");
    this.sent.push(JSON.parse(m));
  }
  close(code, reason) {
    this.closed = { code, reason };
  }
}
globalThis.WebSocketPair = class {
  constructor() {
    this[0] = new FakeSocket();
    this[1] = new FakeSocket();
  }
};

class FakeStorage {
  constructor() {
    this.data = new Map();
    this.alarm = null;
    this.puts = 0;
  }
  get(k) {
    return Promise.resolve(this.data.get(k));
  }
  put(k, v) {
    this.puts++;
    this.data.set(k, v);
    return Promise.resolve();
  }
  delete(k) {
    this.data.delete(k);
    return Promise.resolve();
  }
  deleteAll() {
    this.data.clear();
    return Promise.resolve();
  }
  setAlarm(t) {
    this.alarm = t;
    return Promise.resolve();
  }
  getAlarm() {
    return Promise.resolve(this.alarm);
  }
  deleteAlarm() {
    this.alarm = null;
    return Promise.resolve();
  }
}

class FakeCtx {
  constructor() {
    this.storage = new FakeStorage();
    this.sockets = [];
  }
  acceptWebSocket(ws, tags) {
    ws.tags = tags;
    this.sockets.push(ws);
  }
  getWebSockets(tag) {
    return this.sockets.filter((s) => !s.closed && (!tag || s.tags.includes(tag)));
  }
  getTags(ws) {
    return ws.tags;
  }
}

function makeEnv() {
  const rooms = new Map();
  const env = { PUBLISH_KEY: KEY, RACECAST_VERSION: "v-test", calls: 0 };
  env.ROOMS = {
    idFromName: (n) => n,
    get: (id) => {
      env.calls++;
      if (!rooms.has(id)) rooms.set(id, new TimingRoom(new FakeCtx(), env));
      return rooms.get(id);
    },
  };
  env.rooms = rooms;
  return env;
}

function req(path, headers = {}) {
  return new Request("https://racecast-timing-demo.example.workers.dev" + path, { headers });
}
const WS = { Upgrade: "websocket" };
const PUB = { ...WS, Authorization: `Bearer ${KEY}` };

async function publisher(env, link = LINK) {
  const res = await worker.fetch(req(`/t/${link}/publish?v=1`, PUB), env);
  eq(res.status, 101, "publisher upgrade");
  const room = env.rooms.get(link);
  return { room, ws: room.ctx.sockets.find((s) => s.tags.includes("publisher")) };
}
async function viewer(env, link = LINK) {
  const res = await worker.fetch(req(`/t/${link}/ws`, WS), env);
  if (res.status !== 101) return { status: res.status };
  const room = env.rooms.get(link);
  const socks = room.ctx.getWebSockets("viewer");
  return { status: 101, ws: socks[socks.length - 1] };
}
function state(seq) {
  return JSON.stringify({ t: "state", seq, age_s: 0.5, fallback: "https://f/x", data: { v: 1 } });
}
async function publish(room, ws, text) {
  await room.webSocketMessage(ws, text);
}

setPage("<html>Live timing</html>");

Deno.test("malformed id never reaches a room", async () => {
  const env = makeEnv();
  for (const p of ["/t/short", "/t/../x", `/t/${LINK}/other`, "/x"]) {
    eq((await worker.fetch(req(p), env)).status, 404, p);
  }
  eq(env.calls, 0, "no Durable Object call");
});

Deno.test("wrong publish key is 401 before any room", async () => {
  const env = makeEnv();
  const res = await worker.fetch(req(`/t/${LINK}/publish?v=1`, { ...WS, Authorization: "Bearer x" }), env);
  eq(res.status, 401, "wrong key");
  eq(env.calls, 0, "no Durable Object call");
  eq((await worker.fetch(req(`/t/${LINK}/publish?v=2`, PUB), env)).status, 400, "unknown version");
});

Deno.test("a missing or short publish key refuses every publisher", async () => {
  for (const key of [undefined, "", "short"]) {
    const env = makeEnv();
    env.PUBLISH_KEY = key;
    for (const auth of ["Bearer undefined", "Bearer ", `Bearer ${key}`]) {
      const res = await worker.fetch(req(`/t/${LINK}/publish?v=1`, { ...WS, Authorization: auth }), env);
      eq(res.status, 401, `key ${key} auth ${auth}`);
    }
  }
});

Deno.test("version route", async () => {
  eq(await (await worker.fetch(req("/version"), makeEnv())).text(), "v-test", "version");
});

Deno.test("unknown room 404s the page and the socket", async () => {
  const env = makeEnv();
  eq((await worker.fetch(req(`/t/${LINK}`), env)).status, 404, "page");
  eq((await viewer(env)).status, 404, "socket");
});

Deno.test("first publish makes the room live and fans out to every viewer", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const page = await worker.fetch(req(`/t/${LINK}`), env);
  eq(page.status, 200, "page served");
  assert((await page.text()).includes("Live timing"), "the viewer page");
  const csp = page.headers.get("content-security-policy") || "";
  assert(csp.includes("default-src 'none'") && csp.includes("connect-src 'self'"), `strict CSP: ${csp}`);
  const a = await viewer(env), b = await viewer(env);
  eq(a.ws.sent[0].t, "state", "a late viewer gets the last state at once");
  await publish(room, ws, state(2));
  eq(a.ws.sent.at(-1).seq, 2, "viewer a");
  eq(b.ws.sent.at(-1).seq, 2, "viewer b");
});

Deno.test("requests_today counts page loads and socket upgrades", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  await worker.fetch(req(`/t/${LINK}`), env);
  await viewer(env);
  await publish(room, ws, state(2));
  eq(ws.sent.at(-1).requests_today, 2, "one page load plus one socket");
});

Deno.test("requests_today is stored with the state, not per request", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const before = room.ctx.storage.puts;
  for (let i = 0; i < 20; i++) await viewer(env);
  eq(room.ctx.storage.puts, before, "no storage write per viewer");
});

Deno.test("a viewer that sends is closed", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  await room.webSocketMessage(v.ws, "hello");
  assert(v.ws.closed, "viewers only listen");
});

Deno.test("the page CSP names the wss origin", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const page = await worker.fetch(req(`/t/${LINK}`), env);
  const csp = page.headers.get("content-security-policy");
  assert(csp.includes("wss://racecast-timing-demo.example.workers.dev"), csp);
});

Deno.test("a closing socket is closed by the room", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  await room.webSocketClose(v.ws, 1001, "", true);
  assert(v.ws.closed, "the room completes the close handshake");
});

Deno.test("acks every state with viewers and requests_today", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  await viewer(env);
  await viewer(env);
  await publish(room, ws, state(2));
  eq(ws.sent.at(-1), { t: "ack", seq: 2, viewers: 2, requests_today: 2 }, "ack");
});

Deno.test("publisher close tells viewers, reconnect tells them again", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  ws.closed = { code: 1006 };
  await room.webSocketClose(ws, 1006, "", false);
  eq(v.ws.sent.at(-1), { t: "publisher", up: false }, "publisher down");
  assert(!v.ws.closed, "viewers stay");
  await publisher(env);
  eq(v.ws.sent.at(-1), { t: "publisher", up: true }, "publisher back");
});

Deno.test("a viewer joining while the publisher is away hears it", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  ws.closed = { code: 1006 };
  await room.webSocketClose(ws, 1006, "", false);
  const v = await viewer(env);
  eq(v.ws.sent.map((m) => m.t), ["state", "publisher"], "state then publisher:false");
});

Deno.test("moved is forwarded and kept for late viewers until cleared", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  await publish(room, ws, JSON.stringify({ t: "moved", to: "https://f/x" }));
  eq(v.ws.sent.at(-1), { t: "moved", to: "https://f/x" }, "forwarded");
  const late = await viewer(env);
  eq(late.ws.sent.at(-1), { t: "moved", to: "https://f/x" }, "a late viewer is moved too");
  await publish(room, ws, JSON.stringify({ t: "moved", to: null }));
  const later = await viewer(env);
  assert(!later.ws.sent.some((m) => m.t === "moved"), "cleared");
});

Deno.test("viewer messages are ignored", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  await room.webSocketMessage(v.ws, JSON.stringify({ t: "end" }));
  eq((await worker.fetch(req(`/t/${LINK}`), env)).status, 200, "a viewer cannot end the room");
});

Deno.test("end closes viewers, keeps a tombstone, alarm purges it after 7 days", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  await publish(room, ws, JSON.stringify({ t: "end" }));
  eq(v.ws.sent.at(-1), { t: "end" }, "end forwarded");
  assert(v.ws.closed, "viewer closed");
  const page = await worker.fetch(req(`/t/${LINK}`), env);
  eq(page.status, 410, "ended");
  assert((await page.text()).includes("ended"), "says ended");
  assert(!room.ctx.storage.data.has("state"), "state deleted");
  const at = room.ctx.storage.alarm;
  assert(at >= Date.now() + 7 * DAY - MIN, "alarm in 7 days");
  room.now = () => at;
  await room.alarm();
  eq(room.ctx.storage.data.size, 0, "tombstone purged");
});

Deno.test("30 min without a publisher ends the room via alarm", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const v = await viewer(env);
  ws.closed = { code: 1006 };
  await room.webSocketClose(ws, 1006, "", false);
  const at = room.ctx.storage.alarm;
  assert(at >= Date.now() + 30 * MIN - MIN && at <= Date.now() + 30 * MIN + MIN, "orphan alarm");
  room.now = () => at;
  await room.alarm();
  assert(v.ws.closed, "viewers closed");
  eq((await worker.fetch(req(`/t/${LINK}`), env)).status, 410, "ended");
});

Deno.test("a returning publisher cancels the orphan alarm", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  ws.closed = { code: 1006 };
  await room.webSocketClose(ws, 1006, "", false);
  await publisher(env);
  eq(room.ctx.storage.alarm, null, "alarm cleared");
});

Deno.test("state is persisted at most every 30 s", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  let t = Date.now();
  room.now = () => t;
  for (let i = 1; i <= 15; i++) {
    await publish(room, ws, state(i));
    t += 2000;
  }
  const writes = room.ctx.storage.puts;
  assert(writes <= 4, `about one state write per 30 s, got ${writes} puts`);
  eq(JSON.parse(room.ctx.storage.data.get("state")).seq >= 1, true, "a state is stored");
});

Deno.test("a woken room answers a new viewer from storage", async () => {
  const env = makeEnv();
  const { room, ws } = await publisher(env);
  await publish(room, ws, state(1));
  const fresh = new TimingRoom(room.ctx, env); // eviction: memory gone, storage and sockets stay
  env.rooms.set(LINK, fresh);
  const v = await viewer(env);
  eq(v.ws.sent[0].t, "state", "state from storage");
});
