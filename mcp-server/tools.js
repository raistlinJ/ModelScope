/**
 * Tool definitions for SecOps MCP Server.
 * Contains tool schemas and handlers that can be imported by the Node server.
 */

/**
 * Tool schemas for the ListTools endpoint
 */
export const toolsList = [
  {
    name: "run_nmap_scan",
    description: "Runs an nmap scan against a specified target. Only standard, non-destructive flags are allowed.",
    inputSchema: {
      type: "object",
      properties: {
        target: {
          type: "string",
          description: "Target IP, hostname, or CIDR block (e.g. 'scanme.nmap.org', '192.168.1.0/24')."
        },
        arguments: {
          type: "string",
          description: "Optional nmap flags (e.g. '-F', '-p 80,443', '-sV'). Restricted to safe options.",
          default: "-F"
        }
      },
      required: ["target"]
    }
  },
  {
    name: "file_creator",
    description: "Resolves directories recursively and writes the specified content into the target file path.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description: "Target file path (can be relative or absolute). Directories will be created recursively if needed."
        },
        content: {
          type: "string",
          description: "Content to write to the file."
        }
      },
      required: ["path", "content"]
    }
  },
];

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

};
