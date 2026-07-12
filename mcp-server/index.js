import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
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

// Load tool definitions from JSON file
const toolsList = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));

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
    const handler = toolHandlers[toolName];
    
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
