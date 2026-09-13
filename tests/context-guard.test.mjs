import test from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, readFile, stat, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import ContextGuard from "../deploy/coding-agent/context-guard.mjs";

test("large Japanese tool output is bounded and the original remains retrievable", async () => {
  const directory = await mkdtemp(join(tmpdir(), "qwen-context-test-"));
  const previous = process.env.XDG_DATA_HOME;
  process.env.XDG_DATA_HOME = directory;
  try {
    const hooks = await ContextGuard();
    const original = "日本語の長い検証ログ🧪\n".repeat(3000);
    const output = {title: "test", output: original, metadata: {exit: 0}};
    await hooks["tool.execute.after"]({}, output);
    assert.ok(Buffer.byteLength(output.output) < 6500);
    assert.equal(output.metadata.exit, 0);
    assert.equal(await readFile(output.metadata.context_guard.full_output, "utf8"), original);
    assert.equal((await stat(output.metadata.context_guard.full_output)).mode & 0o777, 0o600);
    assert.ok(!output.output.includes("�"));
    const short = {output: "ok", metadata: {exit: 0}};
    await hooks["tool.execute.after"]({}, short);
    assert.deepEqual(short, {output: "ok", metadata: {exit: 0}});
  } finally {
    if (previous === undefined) delete process.env.XDG_DATA_HOME;
    else process.env.XDG_DATA_HOME = previous;
    await rm(directory, {recursive: true});
  }
});
