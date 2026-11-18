import { registerApp } from "@larksuite/channel";
import fs from "fs";

const OUT = "/tmp/feishu-register-result.json";
const ERR = "/tmp/feishu-register-stderr.txt";

async function main() {
  try {
    const result = await registerApp({
      source: "claudeteam",
      createOnly: true,
      appPreset: { name: "ClaudeTeam-wsm", desc: "ClaudeTeam multi-agent workspace" },
    });
    fs.writeFileSync(OUT, JSON.stringify(result, null, 2));
    process.stderr.write(JSON.stringify({ event: "register_done", result_keys: Object.keys(result || {}) }) + "\n");
  } catch (e) {
    fs.writeFileSync(OUT, JSON.stringify({
      error: e?.message || String(e),
      code: e?.code,
      url: e?.url || "",
      device_code: e?.device_code || "",
    }, null, 2));
  }
}
main();
