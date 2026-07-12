/**
 * Tool definitions for SecOps MCP Server.
 * Contains tool schemas and handlers that can be imported by the Node server.
 */

/**
 * Tool handlers for the CallTool endpoint
 */
export const toolHandlers = {
  /**
   * Handle nmap scan tool calls
   */
  run_nmap_scan: async (request) => {
    const { exec } = await import("child_process");
    const { promisify } = await import("util");
    const execAsync = promisify(exec);
    
    const target = request.params.arguments?.target;
    const args = request.params.arguments?.arguments || "-F";

    if (!target) {
      return { 
        content: [{ type: "text", text: "Error: Target is required." }], 
        isError: true 
      };
    }

    if (!/^[a-zA-Z0-9.\-/]+$/.test(target)) {
      return { 
        content: [{ type: "text", text: "Error: Invalid target format." }], 
        isError: true 
      };
    }

    if (!/^[a-zA-Z0-9\s,\-]*$/.test(args)) {
      return { 
        content: [{ type: "text", text: "Error: Disallowed characters in arguments." }], 
        isError: true 
      };
    }

    try {
      const { stdout, stderr } = await execAsync(`nmap ${args} ${target}`);
      return { 
        content: [{ type: "text", text: stdout || stderr || "Scan completed with no output." }] 
      };
    } catch (error) {
      return {
        content: [{ 
          type: "text", 
          text: `Failed to execute nmap.\nError: ${error.message}\nStderr: ${error.stderr || "None"}` 
        }],
        isError: true
      };
    }
  },

  /**
   * Handle file creator tool calls
   */
  file_creator: async (request) => {
    const fs = await import("fs");
    const path = await import("path");
    
    const filePath = request.params.arguments?.path;
    const content = request.params.arguments?.content;

    if (!filePath) {
      return { 
        content: [{ type: "text", text: "Error: File path is required." }], 
        isError: true 
      };
    }

    if (content === undefined) {
      return { 
        content: [{ type: "text", text: "Error: Content is required." }], 
        isError: true 
      };
    }

    try {
      // Resolve the path (making it absolute and resolving any '..' or '.')
      const resolvedPath = path.resolve(filePath);
      
      // Ensure parent directory exists
      const dirname = path.dirname(resolvedPath);
      if (!fs.existsSync(dirname)) {
        fs.mkdirSync(dirname, { recursive: true });
      }
      
      // Write content to file
      fs.writeFileSync(resolvedPath, content, 'utf8');
      
      return { 
        content: [{ 
          type: "text", 
          text: JSON.stringify({
            status: "success",
            message: `File created successfully at ${resolvedPath}`,
            path: resolvedPath,
            bytes_written: Buffer.byteLength(content, 'utf8')
          }, null, 2)
        }] 
      };
    } catch (error) {
      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            status: "error",
            message: `Failed to create file: ${error.message}`,
            path: filePath
          }, null, 2)
        }],
        isError: true
      };
    }
  },

  read_file: async (request) => {
    const fs = await import("fs");
    const path = await import("path");
    const filePath = request.params.arguments?.path;
    const requestedMax = Number(request.params.arguments?.max_chars ?? 20000);
    const maxChars = Number.isFinite(requestedMax) ? Math.max(1, Math.min(Math.floor(requestedMax), 100000)) : 20000;
    if (!filePath) {
      return { content: [{ type: "text", text: "Error: File path is required." }], isError: true };
    }
    try {
      const resolvedPath = path.resolve(filePath);
      const content = fs.readFileSync(resolvedPath, "utf8");
      const truncated = content.length > maxChars;
      return {
        content: [{
          type: "text",
          text: JSON.stringify({ path: resolvedPath, content: content.slice(0, maxChars), truncated }, null, 2),
        }],
      };
    } catch (error) {
      return { content: [{ type: "text", text: `Failed to read file: ${error.message}` }], isError: true };
    }
  },

  write_file: async (request) => {
    const fs = await import("fs");
    const path = await import("path");
    const filePath = request.params.arguments?.path;
    const content = request.params.arguments?.content;
    if (!filePath || content === undefined) {
      return { content: [{ type: "text", text: "Error: path and content are required." }], isError: true };
    }
    try {
      const resolvedPath = path.resolve(filePath);
      fs.mkdirSync(path.dirname(resolvedPath), { recursive: true });
      fs.writeFileSync(resolvedPath, content, "utf8");
      return {
        content: [{
          type: "text",
          text: JSON.stringify({ status: "success", path: resolvedPath, bytes_written: Buffer.byteLength(content, "utf8") }, null, 2),
        }],
      };
    } catch (error) {
      return { content: [{ type: "text", text: `Failed to write file: ${error.message}` }], isError: true };
    }
  },

  terminal_execute: async (request) => {
    const command = request.params.arguments?.command;
    const requestedTimeout = Number(request.params.arguments?.timeout_seconds ?? 30);
    const timeoutSeconds = Number.isFinite(requestedTimeout)
      ? Math.max(1, Math.min(Math.floor(requestedTimeout), 120))
      : 30;
    if (!command || !command.trim()) {
      return { content: [{ type: "text", text: "Error: Command is required." }], isError: true };
    }
    try {
      const { exec } = await import("child_process");
      const { promisify } = await import("util");
      const { stdout, stderr } = await promisify(exec)(command, {
        cwd: process.cwd(), timeout: timeoutSeconds * 1000, maxBuffer: 1024 * 1024,
      });
      return {
        content: [{ type: "text", text: JSON.stringify({ stdout, stderr, exit_code: 0 }, null, 2) }],
      };
    } catch (error) {
      return {
        content: [{
          type: "text",
          text: JSON.stringify({ stdout: error.stdout || "", stderr: error.stderr || error.message, exit_code: error.code ?? 1 }, null, 2),
        }],
        isError: true,
      };
    }
  },

};
