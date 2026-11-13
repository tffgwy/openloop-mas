// OpenLoop MAS MCP client - pure Node, zero dependencies.
//
// The DSH glue lives in ./index.js. This module only speaks the OpenLoop MAS
// MCP protocol (newline delimited JSON-RPC 2.0 over stdio) to the local
// runtime, so it can be unit tested without the DSH host.
//
// Why MCP and not a shell-out: the MAS runtime keeps an append-only ledger and
// a ticket queue. Driving it through MCP means the same call path a Codex or
// Claude Code client would use, and the plugin never has to parse CLI output.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, delimiter } from "node:path";

/**
 * Resolve the python interpreter that can import the mas package.
 * Priority: explicit config -> the interpreter recorded next to the runtime ->
 * plain `python` on PATH.
 */
export function resolvePython(config) {
  if (config.python && config.python.trim() !== "") return config.python.trim();
  if (config.masHome && config.masHome.trim() !== "") {
    const candidates = [
      join(config.masHome, "bin", "python.exe"),
      join(config.masHome, "python", "python.exe")
    ];
    for (const candidate of candidates) {
      if (existsSync(candidate)) return candidate;
    }
  }
  return "python";
}

/** Resolve the directory that contains the mas package, if we can find one. */
export function resolveAppDir(config) {
  if (!config.masHome || config.masHome.trim() === "") return "";
  const candidate = join(config.masHome, "app");
  return existsSync(candidate) ? candidate : "";
}

/**
 * One JSON-RPC request against the MAS MCP server.
 * The server is newline delimited JSON-RPC 2.0: one JSON object per line.
 */
export function callMcp(config, method, params, id) {
  const python = resolvePython(config);
  const appDir = resolveAppDir(config);
  const args = ["-X", "utf8", "-m", "mas.mcp_server"];
  const env = { ...process.env };
  if (appDir !== "") {
    env.PYTHONPATH = appDir + (env.PYTHONPATH ? delimiter + env.PYTHONPATH : "");
  }
  if (config.masHome && config.masHome.trim() !== "") env.MAS_HOME = config.masHome;

  return new Promise((resolve, reject) => {
    const child = spawn(python, args, { env, stdio: ["pipe", "pipe", "pipe"] });
    let buffer = "";
    let settled = false;
    let stderr = "";

    const timeoutMs = config.timeoutMs && config.timeoutMs > 0 ? config.timeoutMs : 900000;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error(`OpenLoop MAS call timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    const fail = (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    };

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      let index;
      while ((index = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (line === "") continue;
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          continue;
        }
        if (message.id !== id) continue;
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (message.error) {
          const detail = message.error.message ?? JSON.stringify(message.error);
          reject(new Error(`OpenLoop MAS error: ${detail}`));
          return;
        }
        resolve(message.result);
      }
    });

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });

    child.on("error", (err) => {
      fail(new Error(`cannot start the OpenLoop MAS runtime (${python}): ${err.message}`));
    });

    child.on("close", (code) => {
      if (settled) return;
      fail(
        new Error(
          `OpenLoop MAS runtime exited with code ${code}` +
            (stderr.trim() === "" ? "" : `: ${stderr.trim().slice(-400)}`)
        )
      );
    });

    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    child.stdin.end();
  });
}

/**
 * Call a MAS MCP tool and return its parsed payload.
 * The server answers with a single text content block holding the JSON body.
 */
export async function callTool(config, toolName, args) {
  const result = await callMcp(config, "tools/call", { name: toolName, arguments: args ?? {} }, 1);
  const content = result?.content ?? [];
  const texts = content
    .filter((part) => part && part.type === "text")
    .map((part) => part.text);
  if (texts.length === 0) return result ?? null;
  try {
    return JSON.parse(texts.join("\n"));
  } catch {
    return texts.join("\n");
  }
}

/** List the tools the runtime exposes. */
export async function listTools(config) {
  const result = await callMcp(config, "tools/list", {}, 1);
  return result?.tools ?? [];
}
