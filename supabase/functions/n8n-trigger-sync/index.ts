import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const N8N_WEBHOOK_BASE =
  Deno.env.get("N8N_WEBHOOK_BASE") ?? "https://apukuski.app.n8n.cloud/webhook";

/** workflowId -> production webhook path (must match N8N Webhook Trigger nodes) */
const WEBHOOK_PATHS: Record<string, string> = {
  "9hWlvNyCs8HmfZly": "orders-hot-sync",
  "CcOBd7IELOnbonYL": "orders-warm-sync",
  "QKbM3UvkJ8Yjkhb1": "orders-cool-sync",
  "cgEcgz89U6Rp7UJH": "routes-sync",
  "D62F3xpZ443ZFUwa": "reference-sync",
  "JH2On4vSuJidzbyU": "backfill-sync",
};

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type",
};

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const { workflowId } = await req.json();

    if (!workflowId) {
      return new Response(
        JSON.stringify({ error: "workflowId is required" }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const webhookPath = WEBHOOK_PATHS[workflowId];
    if (!webhookPath) {
      return new Response(
        JSON.stringify({
          error: "Unknown workflowId",
          supported: Object.keys(WEBHOOK_PATHS),
        }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const webhookUrl = `${N8N_WEBHOOK_BASE}/${webhookPath}`;
    const execRes = await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "lovable", workflowId }),
    });

    const bodyText = await execRes.text();
    let detail: unknown = bodyText;
    try {
      detail = JSON.parse(bodyText);
    } catch {
      // keep raw text
    }

    if (!execRes.ok) {
      return new Response(
        JSON.stringify({
          success: false,
          workflowId,
          webhookUrl,
          status: execRes.status,
          detail,
        }),
        {
          status: 502,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    return new Response(
      JSON.stringify({
        success: true,
        workflowId,
        webhookUrl,
        detail,
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  } catch (e) {
    return new Response(
      JSON.stringify({ error: String(e) }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      },
    );
  }
});
