// The league's timing host (#1161): a Cloudflare Worker that fans the relay's public
// live timing out to viewers. One Durable Object room per timing link id; the relay
// publishes over one WebSocket, viewers attach through the Hibernation API.
// Deploy bundles this module with page.js (the viewer page as a string) and index.js.

const LINK_ID = /^[A-Za-z0-9_-]{16,64}$/;
const ROUTE = /^\/t\/([^/]+)(\/ws|\/publish)?$/;
const PROTOCOL_V = "1";
const PERSIST_EVERY_MS = 30 * 1000;
const ORPHAN_MS = 30 * 60 * 1000;
const TOMBSTONE_MS = 7 * 24 * 60 * 60 * 1000;

let PAGE = "";

export function setPage(html) {
  PAGE = html;
}

function text(body, status, type = "text/plain; charset=utf-8") {
  return new Response(body, {
    status,
    headers: { "content-type": type, "cache-control": "no-store" },
  });
}

function sameKey(given, expected) {
  if (typeof given !== "string" || typeof expected !== "string" || !expected) return false;
  let diff = given.length ^ expected.length;
  for (let i = 0; i < given.length; i++) {
    diff |= given.charCodeAt(i) ^ expected.charCodeAt(i % expected.length);
  }
  return diff === 0;
}

function utcDay(ms) {
  return Math.floor(ms / 86400000);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/version") return text(env.RACECAST_VERSION || "", 200);
    const m = url.pathname.match(ROUTE);
    if (!m || !LINK_ID.test(m[1])) return text("not found", 404);
    const isSocket = m[2] === "/ws" || m[2] === "/publish";
    if (m[2] === "/publish") {
      const auth = request.headers.get("Authorization") || "";
      if (!sameKey(auth, `Bearer ${env.PUBLISH_KEY}`)) return text("unauthorized", 401);
      if (url.searchParams.get("v") !== PROTOCOL_V) return text("unsupported protocol version", 400);
    }
    if (isSocket && request.headers.get("Upgrade") !== "websocket") {
      return text("expected a WebSocket upgrade", 426);
    }
    if (!isSocket && request.method !== "GET") return text("method not allowed", 405);
    return env.ROOMS.get(env.ROOMS.idFromName(m[1])).fetch(request);
  },
};

export class TimingRoom {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.loaded = false;
    this.phase = null; // null (unknown) | "live" | "ended"
    this.state = null;
    this.moved = null;
    this.day = null;
    this.connsToday = 0;
    this.lastPersist = 0;
  }

  now() {
    return Date.now();
  }

  async load() {
    if (this.loaded) return;
    const s = this.ctx.storage;
    this.phase = (await s.get("phase")) ?? null;
    this.state = (await s.get("state")) ?? null;
    this.moved = (await s.get("moved")) ?? null;
    this.day = (await s.get("day")) ?? null;
    this.connsToday = (await s.get("conns_today")) ?? 0;
    this.loaded = true;
  }

  viewers() {
    return this.ctx.getWebSockets("viewer");
  }

  publishers() {
    return this.ctx.getWebSockets("publisher");
  }

  broadcast(message) {
    for (const ws of this.viewers()) {
      try {
        ws.send(message);
      } catch (_e) {
        // a viewer that went away; the runtime closes it
      }
    }
  }

  async fetch(request) {
    await this.load();
    const path = new URL(request.url).pathname;
    if (path.endsWith("/publish")) return this.acceptPublisher();
    if (path.endsWith("/ws")) return this.acceptViewer();
    if (this.phase === "live") return text(PAGE, 200, "text/html; charset=utf-8");
    if (this.phase === "ended") return text("this timing link has ended", 410);
    return text("unknown timing link", 404);
  }

  async acceptPublisher() {
    if (this.phase === "ended") return text("this timing link has ended", 410);
    for (const old of this.publishers()) old.close(1000, "replaced by a newer publisher");
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1], ["publisher"]);
    await this.ctx.storage.deleteAlarm();
    if (this.phase === "live") this.broadcast(JSON.stringify({ t: "publisher", up: true }));
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  async acceptViewer() {
    if (this.phase === "ended") return text("this timing link has ended", 410);
    if (this.phase !== "live") return text("unknown timing link", 404);
    const pair = new WebSocketPair();
    const ws = pair[1];
    this.ctx.acceptWebSocket(ws, ["viewer"]);
    const day = utcDay(this.now());
    if (day !== this.day) {
      this.day = day;
      this.connsToday = 0;
      await this.ctx.storage.put("day", day);
    }
    this.connsToday++;
    await this.ctx.storage.put("conns_today", this.connsToday);
    if (this.state) ws.send(this.state);
    if (this.moved) ws.send(JSON.stringify({ t: "moved", to: this.moved }));
    if (this.publishers().length === 0) ws.send(JSON.stringify({ t: "publisher", up: false }));
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  async webSocketMessage(ws, message) {
    if (!this.ctx.getTags(ws).includes("publisher")) return; // viewers only listen
    await this.load();
    let msg;
    try {
      msg = JSON.parse(message);
    } catch (_e) {
      return;
    }
    if (msg.t === "state") {
      await this.onState(ws, message, msg);
    } else if (msg.t === "moved") {
      this.moved = typeof msg.to === "string" ? msg.to : null;
      await this.ctx.storage.put("moved", this.moved);
      this.broadcast(JSON.stringify({ t: "moved", to: this.moved }));
    } else if (msg.t === "end") {
      await this.end();
    }
  }

  async onState(ws, raw, msg) {
    const now = this.now();
    this.state = raw;
    if (this.phase !== "live") {
      this.phase = "live";
      await this.ctx.storage.put("phase", "live");
    }
    if (now - this.lastPersist >= PERSIST_EVERY_MS) {
      this.lastPersist = now;
      await this.ctx.storage.put("state", raw);
    }
    this.broadcast(raw);
    ws.send(JSON.stringify({
      t: "ack",
      seq: msg.seq,
      viewers: this.viewers().length,
      conns_today: this.connsToday,
    }));
  }

  async webSocketClose(ws, _code, _reason, _clean) {
    if (!this.ctx.getTags(ws).includes("publisher")) return;
    await this.load();
    if (this.phase !== "live") return;
    if (this.publishers().some((p) => p !== ws)) return;
    this.broadcast(JSON.stringify({ t: "publisher", up: false }));
    await this.ctx.storage.setAlarm(this.now() + ORPHAN_MS);
  }

  async webSocketError(ws, _error) {
    await this.webSocketClose(ws, 1011, "", false);
  }

  async end() {
    this.broadcast(JSON.stringify({ t: "end" }));
    for (const ws of this.viewers()) ws.close(1000, "this timing link has ended");
    this.phase = "ended";
    this.state = null;
    this.moved = null;
    await this.ctx.storage.deleteAll();
    await this.ctx.storage.put("phase", "ended");
    await this.ctx.storage.setAlarm(this.now() + TOMBSTONE_MS);
  }

  async alarm() {
    await this.load();
    if (this.phase === "ended") {
      await this.ctx.storage.deleteAll();
      this.phase = null;
      return;
    }
    if (this.phase === "live" && this.publishers().length === 0) await this.end();
  }
}
