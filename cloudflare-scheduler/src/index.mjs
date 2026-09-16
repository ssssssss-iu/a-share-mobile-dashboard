const WORKFLOW_URL = "https://api.github.com/repos/ssssssss-iu/a-share-mobile-dashboard/actions/workflows/publish.yml/dispatches";

async function dispatchWorkflow(env, scheduledTime) {
  if (!env.GH_ACTIONS_TOKEN) throw new Error("Missing GH_ACTIONS_TOKEN secret");

  const response = await fetch(WORKFLOW_URL, {
    method: "POST",
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${env.GH_ACTIONS_TOKEN}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "a-share-dashboard-scheduler"
    },
    body: JSON.stringify({
      ref: "main",
      inputs: {scheduler: "cloudflare", scheduled_time: String(scheduledTime || "")}
    })
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`GitHub workflow dispatch failed: HTTP ${response.status} ${detail.slice(0, 300)}`);
  }
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatchWorkflow(env, controller.scheduledTime));
  },

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return Response.json({status: "ok", service: "a-share-dashboard-scheduler"});
    }
    return new Response("Not found", {status: 404});
  }
};
