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

async function cachedJson(path, env, ms = CACHE_MS) {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < ms) return JSON.parse(hit.body);
  const response = await fetch(API + path, {
    headers: { Authorization: "Bearer " + env.COC_API_KEY, Accept: "application/json" },
  });
  if (!response.ok) return null;
  const body = await response.text();
  if (cache.size > 300) cache.clear();
  cache.set(path, { at: Date.now(), body });
  return JSON.parse(body);
}

function sideSummary(side, size) {
  const attacks = (side.members || []).flatMap((m) => m.attacks || []);
  const best = {};
  for (const a of attacks) best[a.defenderTag] = Math.max(best[a.defenderTag] || 0, a.stars);
  const open = [...Object.values(best).map((v) => 3 - v), ...Array(Math.max(0, size - Object.keys(best).length)).fill(3)].sort((a, b) => b - a);
  const left = Math.max(0, size - attacks.length);
  return {
    tag: side.tag,
    naam: side.name,
    logo: side.badgeUrls?.medium || null,
    sterren: side.stars || 0,
    procent: side.destructionPercentage || 0,
    aanvallen: side.attacks ?? attacks.length,
    max: (side.stars || 0) + open.slice(0, left).reduce((a, b) => a + b, 0),
    verdeling: [3, 2, 1, 0].map((n) => attacks.filter((a) => a.stars === n).length),
  };
}

// Geeft per CWL-oorlog een korte samenvatting en per clan de actuele rondes.
async function cwlStatus(url, env) {
  const tags = (url.searchParams.get("tags") || "").split(",").filter((t) => TAG.test(t)).slice(0, 32);
  const clans = (url.searchParams.get("groepen") || "").split(",").filter((t) => CLANS.includes(t));
  const oorlogen = {};
  await Promise.all(tags.map(async (tag) => {
    const war = await cachedJson(`/clanwarleagues/wars/%23${tag}`, env);
    if (!war?.state) return;
    const size = war.teamSize || 0;
    oorlogen[tag] = {
      status: war.state, grootte: size, start: war.startTime, einde: war.endTime,
      kanten: [sideSummary(war.clan, size), sideSummary(war.opponent, size)],
    };
  }));
  const groepen = {};
  await Promise.all(clans.map(async (clan) => {
    const group = await cachedJson(`/clans/%23${clan}/currentwar/leaguegroup`, env, 300000);
    if (group?.rounds) groepen[clan] = group.rounds.map((r) => r.warTags.filter((t) => t !== "#0").map((t) => t.slice(1)));
  }));
  return new Response(JSON.stringify({ oorlogen, groepen }), { headers: headers() });
}

function upstreamPath(pathname) {
  const [, kind, tag] = pathname.split("/");
  if (kind === "clan" && CLANS.includes(tag)) return `/clans/%23${tag}/currentwar`;
  if (kind === "oorlog" && TAG.test(tag || "")) return `/clanwarleagues/wars/%23${tag}`;
  return null;
}

async function roofStatus(url, env) {
  const tags = (url.searchParams.get("tags") || "").split(",").filter((t) => TAG.test(t)).slice(0, 8);
  const lijst = await Promise.all(tags.map(async (tag) => {
    const data = await cachedJson(`/clans/%23${tag}/capitalraidseasons?limit=1`, env);
    const w = data?.items?.[0];
    if (!w || w.state !== "ongoing") return null;
    return {
      clan: "#" + tag, start: w.startTime, einde: w.endTime, status: w.state, buit: w.capitalTotalLoot,
      raids: w.raidsCompleted, aanvallen: w.totalAttacks, wijken: w.enemyDistrictsDestroyed,
      leden: (w.members || []).map((m) => ({
        tag: m.tag, naam: m.name, aanv: m.attacks || 0, limiet: m.attackLimit || 0,
        bonus: m.bonusAttackLimit || 0, buit: m.capitalResourcesLooted || 0,
      })),
    };
  }));
  return new Response(JSON.stringify(lijst.filter(Boolean)), { headers: headers() });
}

export default {
  // Start elk uur de ophaalronde in GitHub; de planning van GitHub zelf is onbetrouwbaar.
  async scheduled(event, env) {
    const response = await fetch(
      "https://api.github.com/repos/ThijsPros/eye-on-eye-clan/actions/workflows/update.yml/dispatches",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer " + env.GH_DISPATCH_TOKEN,
          Accept: "application/vnd.github+json",
          "User-Agent": "eye-on-eye-clan",
        },
        body: JSON.stringify({ ref: "main" }),
      },
    );
    if (!response.ok) throw new Error("Ophaalronde niet gestart: " + response.status);
  },

  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: headers({ "Access-Control-Allow-Methods": "GET" }) });
    if (request.method !== "GET") return new Response("{}", { status: 405, headers: headers() });
    const url = new URL(request.url);
    if (url.pathname === "/cwl") return cwlStatus(url, env);
    if (url.pathname === "/roof") return roofStatus(url, env);
    const path = upstreamPath(url.pathname);
    if (!path) return new Response("{}", { status: 404, headers: headers() });

    const hit = cache.get(path);
    if (hit && Date.now() - hit.at < CACHE_MS) return new Response(hit.body, { headers: headers() });

    const response = await fetch(API + path, {
      headers: { Authorization: "Bearer " + env.COC_API_KEY, Accept: "application/json" },
    });
    if (!response.ok) return new Response(JSON.stringify({ fout: response.status }), { status: 502, headers: headers() });
    const body = await response.text();
    if (cache.size > 300) cache.clear();
    cache.set(path, { at: Date.now(), body });
    return new Response(body, { headers: headers() });
  },
};
