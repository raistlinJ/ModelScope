import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import express from "express";
import cors from "cors";
import crypto from "crypto";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

// Import tool handlers from tools.js
import { toolHandlers } from "./tools.js";

// Get __dirname equivalent in ES module
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load bundled tool definitions and optional Claude Desktop-compatible stdio
// servers from the same manifest used to render ModelScope's checkboxes.
const bundledTools = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));
const manifestPath = process.env.MCP_CONFIG_FILE || path.join(__dirname, "mcp_config.json");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const externalToolClients = new Map();
const externalTools = [];

for (const [serverName, spec] of Object.entries(manifest.mcpServers || {})) {
  if (!spec || typeof spec !== "object" || spec.transport === "bundled" || !spec.command) continue;
  try {
    const transport = new StdioClientTransport({
      command: spec.command,
      args: spec.args || [],
      env: { ...process.env, ...(spec.env || {}) },
      stderr: "pipe",
    });
    const client = new Client({ name: "modelscope-mcp-gateway", version: "1.0" }, { capabilities: {} });
    await client.connect(transport);
    const result = await client.listTools();
    for (const tool of result.tools || []) {
      if (!tool?.name) continue;
      const exposedName = `${serverName}__${tool.name}`;
      externalTools.push({ ...tool, name: exposedName });
      externalToolClients.set(exposedName, { client, transport, sourceName: tool.name });
    }
  } catch (error) {
    console.error(`[MCP] Failed to start stdio server ${serverName}: ${error.message}`);
  }
}

const allToolsList = [...bundledTools, ...externalTools];
const enabledToolNames = new Set(
  (process.env.MCP_TOOL_NAMES || "").split(",").map((name) => name.trim()).filter(Boolean)
);
const toolsList = enabledToolNames.size
  ? allToolsList.filter((tool) => enabledToolNames.has(tool.name))
  : allToolsList;
const exposedToolNames = new Set(toolsList.map((tool) => tool.name));

const app = express();
const transports = new Map();

app.options("/sse", cors({
  origin: "http://127.0.0.1:8080",
  allowedHeaders: ["content-type", "mcp-session-id", "mcp-protocol-version", "accept"],
  exposedHeaders: ["mcp-session-id"],
  methods: ["POST", "GET", "DELETE", "OPTIONS"],
}));

app.use(cors({
  origin: "http://127.0.0.1:8080",
  allowedHeaders: ["content-type", "mcp-session-id", "mcp-protocol-version", "accept"],
  exposedHeaders: ["mcp-session-id"],
  methods: ["POST", "GET", "DELETE", "OPTIONS"],
}));
app.use(express.json());

function registerHandlers(server) {
  // Register ListTools handler
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: toolsList
  }));

  // Register CallTool handler
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const toolName = request.params.name;
    const external = externalToolClients.get(toolName);
    const handler = exposedToolNames.has(toolName) ? toolHandlers[toolName] : undefined;
    if (external) {
      try {
        return await external.client.callTool({ name: external.sourceName, arguments: request.params.arguments || {} });
      } catch (error) {
        return { content: [{ type: "text", text: `External tool failed: ${error.message}` }], isError: true };
      }
    }
    
    if (!handler) {
      return { 
        content: [{ type: "text", text: `Error: Unknown tool '${toolName}'` }], 
        isError: true 
      };
    }
    
    try {
      return await handler(request);
    } catch (error) {
      return { 
        content: [{ type: "text", text: `Tool execution failed: ${error.message}` }], 
        isError: true 
      };
    }
  });
}

function createServer() {
  const server = new Server(
    { name: "secops-mcp-server", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  registerHandlers(server);
  return server;
}

const isInitializeRequest = (body) =>
  body?.jsonrpc === "2.0" &&
  (body?.method === "initialize" || body?.method === "mcp.initialize");

async function handleMcpRequest(req, res) {
  try {
    const sessionId = req.header("mcp-session-id");

    console.log(`[MCP] ${req.method} | session: ${sessionId ?? "none"} | stored sessions: [${[...transports.keys()].join(", ")}]`);

    let transport = sessionId ? transports.get(sessionId) : undefined;

    if (!transport) {
      if (req.method !== "POST" || !isInitializeRequest(req.body)) {
        console.warn(`[MCP] Rejected — not an initialize request. method=${req.method} sessionId=${sessionId}`);
        res.status(400).json({
          jsonrpc: "2.0",
          error: { code: -32000, message: "Bad Request: missing or invalid session" },
          id: null,
        });
        return;
      }

      console.log("[MCP] Creating new session...");

      const newTransport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => {
          const id = crypto.randomUUID();
          console.log(`[MCP] Generated session ID: ${id}`);
          return id;
        },
        onsessioninitialized: (id) => {
          console.log(`[MCP] Session initialized, storing ID: ${id}`);
          transports.set(id, newTransport);
          console.log(`[MCP] Stored sessions: [${[...transports.keys()].join(", ")}]`);
        },
      });

      newTransport.onclose = () => {
        console.log(`[MCP] Session closed: ${newTransport.sessionId}`);
        if (newTransport.sessionId) {
          transports.delete(newTransport.sessionId);
        }
      };

      const server = createServer();
      await server.connect(newTransport);
      await newTransport.handleRequest(req, res, req.body);
      return;
    }

    console.log(`[MCP] Reusing session: ${sessionId}`);
    await transport.handleRequest(req, res, req.body);

  } catch (err) {
    console.error(`[MCP] ${req.method} error:`, err);
    if (!res.headersSent) {
      res.status(500).json({
        error: "Internal Server Error",
        details: err instanceof Error ? err.message : String(err),
      });
    }
  }
}

app.post("/sse", handleMcpRequest);
app.post("/message", handleMcpRequest);

const port = Number(process.env.MCP_PORT || 9191);
app.listen(port, "127.0.0.1", () => {
  console.error(`✅ SecOps MCP Server listening on http://127.0.0.1:${port}/sse`);
});
