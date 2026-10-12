import path from "node:path";
import readline from "node:readline/promises";
import { chromium } from "playwright";

const profile = process.env.LOOM_ROUTER_PROFILE;
if (!profile || !path.isAbsolute(profile)) throw new Error("LOOM_ROUTER_PROFILE must be an explicit absolute path");
const context = await chromium.launchPersistentContext(path.resolve(profile), {
  headless: false,
  channel: process.env.LOOM_ROUTER_CHROME_CHANNEL || "chromium",
});
const page = context.pages()[0] || await context.newPage();
await page.goto("https://chatgpt.com/", { waitUntil: "domcontentloaded" });
console.log("Use this dedicated window to sign in directly at chatgpt.com. Do not paste credentials into this terminal.");
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
await rl.question("After ChatGPT is visibly signed in, press Enter to close the browser and save the dedicated profile: ");
rl.close();
await context.close();
