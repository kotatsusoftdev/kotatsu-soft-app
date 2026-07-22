export const API_BASE_URL = "https://kotatsu-soft-stats.workers.dev";

const ENDPOINTS = {
  stats: "/stats",
  portalView: "/events/pv",
  gamePlay: "/events/play",
};

function buildUrl(path) {
  const base = API_BASE_URL.replace(/\/$/, "");
  return `${base}${path}`;
}

function toCount(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  return Math.max(0, Math.trunc(num));
}

function pickCount(source, keys) {
  if (!source || typeof source !== "object") return null;
  for (const key of keys) {
    const count = toCount(source[key]);
    if (count !== null) return count;
  }
  return null;
}

function parseGamePlayCount(entry) {
  if (entry === null || entry === undefined) return null;
  if (typeof entry === "number") return toCount(entry);
  if (typeof entry !== "object") return null;
  return pickCount(entry, ["plays", "playCount", "count", "value"]);
}

function normalizeStats(payload) {
  if (!payload || typeof payload !== "object") return null;

  const globalFromRoot = payload.global && typeof payload.global === "object" ? payload.global : payload;
  const totalPlays = pickCount(globalFromRoot, ["totalPlays", "total_plays", "plays", "totalGamePlays"]);
  const portalPv = pickCount(globalFromRoot, ["portalPv", "portal_pv", "pv", "totalPv"]);

  const rawGames = payload.games && typeof payload.games === "object" ? payload.games : {};
  const gamePlays = {};

  for (const [gameId, entry] of Object.entries(rawGames)) {
    const count = parseGamePlayCount(entry);
    if (count !== null) {
      gamePlays[gameId] = count;
    }
  }

  return {
    totalPlays,
    portalPv,
    gamePlays,
  };
}

function normalizeIncrementResponse(payload, gameId) {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const stats = normalizeStats(payload) || { totalPlays: null, portalPv: null, gamePlays: {} };

  let gamePlay = null;
  if (gameId) {
    gamePlay = stats.gamePlays[gameId] ?? null;
    if (gamePlay === null) {
      gamePlay = pickCount(payload, ["plays", "playCount", "count", "value"]);
    }
  }

  return {
    totalPlays: stats.totalPlays,
    portalPv: stats.portalPv,
    gamePlay,
  };
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return null;
  }

  return response.json();
}

async function postEvent(path, body, { keepalive = true } = {}) {
  try {
    const payload = await fetchJson(buildUrl(path), {
      method: "POST",
      keepalive,
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    return payload;
  } catch (_error) {
    return null;
  }
}

function sendEventBeacon(path, body) {
  if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
    return false;
  }

  const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
  return navigator.sendBeacon(buildUrl(path), blob);
}

export function formatCount(value) {
  const count = toCount(value);
  if (count === null) return "0";
  return new Intl.NumberFormat("ja-JP").format(count);
}

export async function fetchPortalStats() {
  try {
    const payload = await fetchJson(buildUrl(ENDPOINTS.stats), {
      method: "GET",
      cache: "no-store",
      headers: {
        accept: "application/json",
      },
    });
    return normalizeStats(payload);
  } catch (_error) {
    return null;
  }
}

export async function incrementPortalPv(context = {}) {
  const body = {
    scope: "portal",
    page: context.page || "portal",
    source: context.source || "portal-index",
    ts: Date.now(),
  };

  const payload = await postEvent(ENDPOINTS.portalView, body, { keepalive: true });
  if (payload) {
    return normalizeIncrementResponse(payload, null);
  }

  sendEventBeacon(ENDPOINTS.portalView, body);
  return null;
}

export async function incrementGamePlay(gameId, context = {}) {
  if (!gameId) {
    return null;
  }

  const body = {
    gameId,
    page: context.page || "portal",
    source: context.source || "play-button",
    ts: Date.now(),
  };

  const payload = await postEvent(ENDPOINTS.gamePlay, body, { keepalive: true });
  if (payload) {
    return normalizeIncrementResponse(payload, gameId);
  }

  sendEventBeacon(ENDPOINTS.gamePlay, body);
  return null;
}

/*
Reusable example for game pages (e.g. game over screen):

import { incrementGamePlay, fetchPortalStats } from "../common/stats-client.js";

// Increment when a run starts or when game over happens.
incrementGamePlay("mikan_buster", {
  page: "001_mikan_buster",
  source: "game-over",
});

// Optional: pull latest aggregate stats for in-game UI.
const stats = await fetchPortalStats();
if (stats?.gamePlays?.mikan_buster != null) {
  console.log("Latest plays:", stats.gamePlays.mikan_buster);
}
*/
