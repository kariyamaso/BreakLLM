import {mkdir, writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {homedir} from "node:os";
import {join} from "node:path";

// Bound each tool result before it can overflow a 32K model between compactions.
// Preserve the full text in a private, content-addressed file for selective reads.
const LIMIT = 6000;
export default async function ContextGuard() {
  const data = process.env.XDG_DATA_HOME || join(homedir(), ".local/share");
  const directory = join(data, "opencode", "qwen-tool-output");
  return {
    "tool.execute.after": async (_input, output) => {
      if (typeof output.output !== "string") return;
      const bytes = Buffer.from(output.output);
      if (bytes.length <= LIMIT) return;
      await mkdir(directory, {recursive: true, mode: 0o700});
      const path = join(directory, createHash("sha256").update(bytes).digest("hex") + ".txt");
      await writeFile(path, bytes, {mode: 0o600});
      const prefix = new TextDecoder().decode(bytes.subarray(0, LIMIT), {stream: true});
      output.output = `${prefix}\n\n[Tool output shortened from ${bytes.length} bytes. Full text: ${path}. Read only the required lines.]`;
      output.metadata = {...output.metadata, context_guard: {original_bytes: bytes.length, full_output: path}};
    },
  };
}
