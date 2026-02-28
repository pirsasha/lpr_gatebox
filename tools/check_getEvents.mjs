import { getEvents } from "../ui/src/api.js";

const captured = [];
global.fetch = async (url) => {
  captured.push(String(url));
  return {
    ok: true,
    json: async () => ({ ok: true }),
    text: async () => "",
  };
};

const cases = [
  { name: "legacy numeric + opts", args: [30, { include_debug: true }], expect: "/api/v1/events?limit=30&include_debug=1" },
  { name: "object signature", args: [{ limit: 40, include_debug: false }], expect: "/api/v1/events?limit=40" },
  { name: "object with after_ts", args: [{ limit: 200, after_ts: 12345, include_debug: true }], expect: "/api/v1/events?limit=200&after_ts=12345&include_debug=1" },
  { name: "undefined limit fallback", args: [{ limit: undefined }], expect: "/api/v1/events?limit=30" },
  { name: "zero limit clamped", args: [{ limit: 0 }], expect: "/api/v1/events?limit=1" },
  { name: "NaN limit fallback", args: [{ limit: Number.NaN }], expect: "/api/v1/events?limit=30" },
  { name: "mixed signature merge", args: [{ limit: 25 }, { include_debug: true, after_ts: 7 }], expect: "/api/v1/events?limit=25&after_ts=7&include_debug=1" },
  { name: "object after_ts dropped", args: [{ limit: 10, after_ts: { bad: true } }], expect: "/api/v1/events?limit=10" },
];

for (const test of cases) {
  captured.length = 0;
  await getEvents(...test.args);
  const got = captured[0] || "";

  if (got !== test.expect) {
    console.error(`[FAIL] ${test.name}`);
    console.error(`  expected: ${test.expect}`);
    console.error(`  got:      ${got}`);
    process.exit(1);
  }

  if (got.includes("limit=undefined") || got.includes("limit=NaN") || got.includes("limit=0") || got.includes("[object Object]")) {
    console.error(`[FAIL] ${test.name} produced malformed URL: ${got}`);
    process.exit(1);
  }

  console.log(`[OK] ${test.name} -> ${got}`);
}

console.log("All getEvents URL checks passed.");
