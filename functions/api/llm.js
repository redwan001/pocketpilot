// Cloudflare Pages Function: POST /api/llm
// Forwards PocketPilot's planner requests to Workers AI (binding "AI"). No API key ever reaches the browser.
//
// Hardening (this endpoint must not become a free general-purpose LLM proxy):
//  - only PocketPilot conversations: the system prompt must be PocketPilot's, tools must be PocketPilot's tools
//  - size limits on the request, the number of messages and the output tokens
//  - per-IP rate limit (best effort, per edge isolate)
//  - fixed allowlist of models; the client cannot name arbitrary models

// Models that support tool calling on the Workers Free plan. Qwen3 30B scored best in scripts/eval_models.py.
const MODELS = {
  "qwen3-30b": "@cf/qwen/qwen3-30b-a3b-fp8",
  "gemma-4-26b": "@cf/google/gemma-4-26b-a4b-it",
  "gpt-oss-120b": "@cf/openai/gpt-oss-120b",
};
const DEFAULT_MODEL = "qwen3-30b";
const TOOLS = new Set([
  "get_balances", "list_transactions", "spending_summary", "compare_months", "find_recurring_bills", "find_unusual_activity",
  "get_budget_plan", "forecast_month_end", "get_insights", "check_affordability", "plan_savings_goal",
  "set_category_budget", "move_to_savings", "cancel_subscription", "pay_bill", "transfer_to_payee", "dispute_transaction",
]);
const MAX_BODY = 200_000, MAX_MESSAGES = 60, RATE_PER_MIN = 30;
const hits = new Map();

const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

function rateLimited(ip) {
  const now = Date.now(), list = (hits.get(ip) || []).filter(t => now - t < 60_000);
  list.push(now); hits.set(ip, list);
  if (hits.size > 5000) hits.clear();
  return list.length > RATE_PER_MIN;
}

function normalize(out) {
  if (out && Array.isArray(out.choices)) {                    // OpenAI-compatible shape
    const m = out.choices[0]?.message || {};
    return { content: m.content || "", tool_calls: (m.tool_calls || []).map((t, i) => ({
      id: t.id || `call_${i}`, name: t.function?.name ?? t.name, arguments: t.function?.arguments ?? t.arguments ?? {} })) };
  }
  return { content: out?.response ?? "", tool_calls: (out?.tool_calls || []).map((t, i) => ({   // classic Workers AI shape
    id: t.id || `call_${i}`, name: t.name, arguments: t.arguments ?? {} })) };
}

export async function onRequestPost({ request, env }) {
  if (!env.AI) return json({ error: "no_ai_binding" }, 503);
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (rateLimited(ip)) return json({ error: "rate_limited" }, 429);
  const raw = await request.text();
  if (raw.length > MAX_BODY) return json({ error: "too_large" }, 413);
  let body;
  try { body = JSON.parse(raw); } catch { return json({ error: "bad_json" }, 400); }

  const { messages, tools } = body;
  if (!Array.isArray(messages) || messages.length < 2 || messages.length > MAX_MESSAGES) return json({ error: "bad_messages" }, 400);
  if (messages[0]?.role !== "system" || !String(messages[0].content || "").startsWith("You are PocketPilot")) return json({ error: "forbidden" }, 403);
  // tools are optional (the Guardian reviewer runs without any); when present they must all be PocketPilot's
  if (tools !== undefined && (!Array.isArray(tools) || tools.some(t => !TOOLS.has(t?.function?.name)))) return json({ error: "forbidden_tools" }, 403);

  const key = MODELS[body.model] ? body.model : DEFAULT_MODEL;
  try {
    const input = { messages, max_tokens: 1500, temperature: 0.2 };
    if (Array.isArray(tools) && tools.length) input.tools = tools;
    const out = await env.AI.run(MODELS[key], input);
    return json({ model: key, ...normalize(out) });
  } catch (e) {
    const msg = String(e?.message || e);
    return json({ error: /limit|quota|neuron|429/i.test(msg) ? "quota_exceeded" : "ai_error", detail: msg.slice(0, 200) }, 502);
  }
}

export const onRequest = () => json({ error: "method_not_allowed" }, 405);
