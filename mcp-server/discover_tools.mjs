// Emit a normalized tool list for bundled and Claude Desktop-style stdio MCP
// entries. Used by the ModelScope configuration UI during validation.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const configPath = process.argv[2] || path.join(__dirname, "mcp_config.json");
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
const builtinTools = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));

if (!config || typeof config !== "object" || !config.mcpServers || typeof config.mcpServers !== "object") {
  throw new Error('MCP config must contain an "mcpServers" object');
}

const records = [];
for (const [server, spec] of Object.entries(config.mcpServers)) {
  if (!spec || typeof spec !== "object") throw new Error(`MCP server "${server}" must be an object`);
  const label = typeof spec.label === "string" && spec.label ? spec.label : server;
  if (spec.transport === "bundled") {
    if (!Array.isArray(spec.tools) || !spec.tools.length) throw new Error(`Bundled server "${server}" needs a non-empty tools list`);
    for (const name of spec.tools) {
      const tool = builtinTools.find((candidate) => candidate.name === name);
      if (!tool) throw new Error(`Unknown bundled tool "${name}"`);
      if (tool.server !== server) throw new Error(`Tool "${name}" belongs to "${tool.server}", not "${server}"`);
      records.push({ server, label, tool_name: name, source_name: name, description: tool.description || "", inputSchema: tool.inputSchema, transport: "bundled" });
    }
    continue;
  }
  if (typeof spec.command !== "string" || !spec.command) throw new Error(`MCP server "${server}" needs either transport:"bundled" or a command`);
  if (spec.args !== undefined && !Array.isArray(spec.args)) throw new Error(`MCP server "${server}" args must be an array`);
  if (spec.env !== undefined && (typeof spec.env !== "object" || Array.isArray(spec.env))) throw new Error(`MCP server "${server}" env must be an object`);

  const transport = new StdioClientTransport({
    command: spec.command,
    args: spec.args || [],
    env: { ...process.env, ...(spec.env || {}) },
    stderr: "pipe",
  });
  const client = new Client({ name: "modelscope-config", version: "1.0" }, { capabilities: {} });
  try {
    await client.connect(transport);
    const result = await client.listTools();
    for (const tool of result.tools || []) {
      if (!tool?.name) continue;
      records.push({
        server, label,
        tool_name: `${server}__${tool.name}`,
        source_name: tool.name,
        description: tool.description || "",
        inputSchema: tool.inputSchema || { type: "object", properties: {} },
        transport: "stdio",
      });
    }
  } finally {
    await transport.close().catch(() => {});
  }
}
process.stdout.write(JSON.stringify(records));
