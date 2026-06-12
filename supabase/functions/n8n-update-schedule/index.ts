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
    const { workflowId, cronExpression } = await req.json();

    if (!workflowId || !cronExpression) {
      return new Response(
        JSON.stringify({ error: "workflowId and cronExpression are required" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Fetch current workflow to get full definition
    const getRes = await fetch(`${N8N_API_URL}/workflows/${workflowId}`, {
      headers: { "X-N8N-API-KEY": N8N_API_KEY },
    });

    if (!getRes.ok) {
      const err = await getRes.text();
      return new Response(
        JSON.stringify({ error: "Failed to fetch workflow", detail: err }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const workflow = await getRes.json();

    // Update the cron expression in the Schedule Trigger node
    const updatedNodes = workflow.nodes.map((node: any) => {
      if (node.type === "n8n-nodes-base.scheduleTrigger") {
        return {
          ...node,
          parameters: {
            ...node.parameters,
            rule: { interval: [{ field: "cronExpression", expression: cronExpression }] },
          },
        };
      }
      return node;
    });

    // PUT updated workflow back
    const putRes = await fetch(`${N8N_API_URL}/workflows/${workflowId}`, {
      method: "PUT",
      headers: {
        "X-N8N-API-KEY": N8N_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ...workflow, nodes: updatedNodes }),
    });

    if (!putRes.ok) {
      const err = await putRes.text();
      return new Response(
        JSON.stringify({ error: "Failed to update workflow", detail: err }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const updated = await putRes.json();
    return new Response(
      JSON.stringify({ success: true, workflowId, cronExpression, name: updated.name }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  } catch (e) {
    return new Response(
      JSON.stringify({ error: String(e) }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
