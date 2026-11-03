// Live-koppeling: geeft de site actuele oorlogsdata zonder de API-sleutel te tonen.
const CLANS = ["PYQ2RLCQ", "2RGYGLVC0", "2JYY0809P", "2CJY8LJU0"];
const ORIGIN = "https://thijspros.github.io";
const API = "https://cocproxy.royaleapi.dev/v1";
const TAG = /^[0289PYLQGRJCUV]{3,12}$/;
const CACHE_MS = 25000;
const cache = new Map();

function headers(extra = {}) {
  return {
    "Access-Control-Allow-Origin": ORIGIN,
    "Cache-Control": "no-store",
    "Content-Type": "application/json",
    ...extra,
  };
}

function upstreamPath(pathname) {
  const [, kind, tag] = pathname.split("/");
  if (kind === "clan" && CLANS.includes(tag)) return `/clans/%23${tag}/currentwar`;
  if (kind === "oorlog" && TAG.test(tag || "")) return `/clanwarleagues/wars/%23${tag}`;
  return null;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: headers({ "Access-Control-Allow-Methods": "GET" }) });
    if (request.method !== "GET") return new Response("{}", { status: 405, headers: headers() });
    const path = upstreamPath(new URL(request.url).pathname);
    if (!path) return new Response("{}", { status: 404, headers: headers() });

    const hit = cache.get(path);
    if (hit && Date.now() - hit.at < CACHE_MS) return new Response(hit.body, { headers: headers() });

    const response = await fetch(API + path, {
      headers: { Authorization: "Bearer " + env.COC_API_KEY, Accept: "application/json" },
    });
    if (!response.ok) return new Response(JSON.stringify({ fout: response.status }), { status: 502, headers: headers() });
    const body = await response.text();
    if (cache.size > 50) cache.clear();
    cache.set(path, { at: Date.now(), body });
    return new Response(body, { headers: headers() });
  },
};
