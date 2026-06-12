import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const N8N_API_URL = Deno.env.get("N8N_API_URL")!;
const N8N_API_KEY = Deno.env.get("N8N_API_KEY")!;

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
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Fetch workflow to find the webhook or trigger node
    const getRes = await fetch(`${N8N_API_URL}/workflows/${workflowId}`, {
      headers: { "X-N8N-API-KEY": N8N_API_KEY },
    });

    if (!getRes.ok) {
      return new Response(
        JSON.stringify({ error: "Workflow not found" }),
        { status: 404, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Deactivate, then re-activate to force immediate run isn't reliable.
    // Instead POST to the N8N executions endpoint with the workflow ID.
    const execRes = await fetch(`${N8N_API_URL}/executions`, {
      method: "POST",
      headers: {
        "X-N8N-API-KEY": N8N_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ workflowId }),
    });

    const execData = await execRes.json();

    if (!execRes.ok) {
      // N8N cloud may not support POST /executions — fall back to reporting
      return new Response(
        JSON.stringify({
          success: false,
          message: "Manual trigger not supported via API on N8N cloud — use webhook trigger",
          detail: execData,
        }),
        { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({ success: true, executionId: execData.id, workflowId }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  } catch (e) {
    return new Response(
      JSON.stringify({ error: String(e) }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
