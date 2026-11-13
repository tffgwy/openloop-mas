// OpenLoop MAS plugin for DeepSeek Harness.
//
// The plugin does NOT re-implement the loop. It speaks the OpenLoop MAS MCP
// protocol (newline delimited JSON-RPC 2.0 over stdio) to the local runtime,
// so the DSH conversation and the MAS ledger always agree on one truth.
//
// The MCP client itself lives in ./mcp-client.js and has zero dependencies,
// which keeps it unit testable without the DSH host.

import Schema from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { callTool, resolvePython, resolveAppDir } from "./mcp-client.js";

const name = "openloop-mas";
const inject = ["tools"];

const Config = Schema.object({
  // Directory that holds the OpenLoop MAS runtime (the one install.ps1 writes).
  masHome: Schema.string().default(""),
  // Explicit interpreter for the runtime. Empty = derive from masHome / PATH.
  python: Schema.string().default(""),
  // Milliseconds before an MCP call is considered dead.
  timeoutMs: Schema.number().default(900000)
});

function jsonOutput() {
  return {
    schema: { type: "json" },
    render: (_args, payload) => [
      { type: "text", text: JSON.stringify(payload, null, 2) }
    ]
  };
}

function apply(ctx, config) {
  const call = (toolName, args) => callTool(config, toolName, args);

  ctx.tools.register(
    defineTool({
      name: "mas_run",
      description:
        "Run an OpenLoop MAS multi-agent loop until it closes. The loop plans, works, verifies against machine-checked acceptance criteria and reflects; it closes only when every blocking criterion passes, otherwise it escalates to a human ticket. Returns status, per-criterion verdicts, vote counts and the ledger path.",
      parameters: {
        goal: { type: "string", required: true, description: "任务目标 / what should be produced" },
        criteria: {
          type: "array",
          description:
            "验收标准列表。每条可带 check 机检规则（contains / not_contains / min_chars / max_chars / regex / lines_min / section / json_keys）。",
          items: {
            type: "object",
            additionalProperties: true,
            properties: {
              id: { type: "string" },
              desc: { type: "string" },
              check: { type: "object", additionalProperties: true },
              required: { type: "boolean" }
            }
          }
        },
        title: { type: "string" },
        team: { type: "string" },
        parallel: { type: "integer", description: "并行候选的 worker 数量（>1 时并发产出多个视角）" },
        perspective: {
          type: "string",
          description: "候选视角：direct / thorough / concise / structured / example_first / risk_first"
        },
        reviewers: { type: "integer", description: "独立评审者数量（>1 时启用对抗评审聚合）" },
        review_mode: { type: "string", description: "多评审聚合规则：all / majority / any" }
      },
      output: jsonOutput(),
      async execute(args) {
        return await call("mas_run", args);
      }
    })
  );

  ctx.tools.register(
    defineTool({
      name: "mas_status",
      description:
        "Show the local OpenLoop MAS state: current configuration, how many runs are recorded, and how many human escalation tickets are still open.",
      parameters: {},
      output: jsonOutput(),
      async execute() {
        return await call("mas_status", {});
      }
    })
  );

  ctx.tools.register(
    defineTool({
      name: "mas_ledger",
      description:
        "Read the append-only OpenLoop MAS ledger (JSONL). Optionally filter by run_id. This is the audit trail of every closed and escalated loop.",
      parameters: {
        run_id: { type: "string" },
        tail: { type: "integer" }
      },
      output: jsonOutput(),
      async execute(args) {
        return await call("mas_ledger", args ?? {});
      }
    })
  );

  ctx.tools.register(
    defineTool({
      name: "mas_tickets",
      description:
        "List the human escalation tickets of OpenLoop MAS. A ticket means the loop could not close on its own and needs a human decision.",
      parameters: { state: { type: "string", description: "waiting_human | resolved" } },
      output: jsonOutput(),
      async execute(args) {
        return await call("mas_tickets", args ?? {});
      }
    })
  );

  ctx.tools.register(
    defineTool({
      name: "mas_report",
      description:
        "Render the Markdown report of a OpenLoop MAS run: goal, verdict, per-criterion evidence and the closure decision.",
      parameters: { run_id: { type: "string" } },
      output: jsonOutput(),
      async execute(args) {
        return await call("mas_report", args ?? {});
      }
    })
  );

  ctx.tools.register(
    defineTool({
      name: "mas_doctor",
      description:
        "Run the OpenLoop MAS installation self-check: runtime importable, skills in sync, provider reachability, ledger health.",
      parameters: {},
      output: jsonOutput(),
      async execute() {
        return await call("mas_doctor", {});
      }
    })
  );

  const python = resolvePython(config);
  const appDir = resolveAppDir(config);
  console.log(
    `[openloop-mas] ready: python=${python}` +
      (appDir === "" ? " (no local app dir; relying on the installed package)" : ` app=${appDir}`)
  );
}

export { Config, apply, inject, name };
export { callTool, callMcp, listTools, resolvePython, resolveAppDir } from "./mcp-client.js";
