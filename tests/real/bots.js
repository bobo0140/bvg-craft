// Ботове-играчи за теста на истински сървър.
// Чете команди от stdin (по един JSON на ред) и пише събития на stdout.
//   node bots.js check <версия>      -> "yes"/"no" дали mineflayer я поддържа
//   node bots.js run <порт> <име1,име2,...>
const mineflayer = require("mineflayer");
const readline = require("readline");

if (process.argv[2] === "check") {
  const want = process.argv[3];
  const ok = (mineflayer.testedVersions || []).includes(want) ||
    require("minecraft-data").versionsByMinecraftVersion.pc[want] !== undefined;
  console.log(ok ? "yes" : "no");
  process.exit(0);
}

const port = parseInt(process.argv[3] || "25565", 10);
const names = (process.argv[4] || "BVG").split(",");
const bots = {};
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");

function make(name) {
  const bot = mineflayer.createBot({
    host: "127.0.0.1", port, username: name, auth: "offline",
    version: false, hideErrors: false, checkTimeoutInterval: 120000,
  });
  bots[name] = bot;
  bot.once("spawn", () => out({ ev: "spawn", bot: name, pos: bot.entity.position }));
  bot.on("messagestr", (m) => out({ ev: "msg", bot: name, text: m }));
  bot.on("kicked", (r) => out({ ev: "kicked", bot: name, reason: String(r) }));
  bot.on("error", (e) => out({ ev: "error", bot: name, error: String(e) }));
  bot.on("end", (r) => out({ ev: "end", bot: name, reason: String(r) }));
  bot.on("death", () => out({ ev: "death", bot: name }));
}

names.forEach((n, i) => setTimeout(() => make(n), i * 2500));

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  let c;
  try { c = JSON.parse(line); } catch { return; }
  const bot = bots[c.bot];
  if (!bot || !bot.entity) return out({ ev: "nobot", bot: c.bot });
  try {
    if (c.chat) bot.chat(c.chat);
    if (c.jump) {
      for (let i = 0; i < c.jump; i++) {
        bot.setControlState("jump", true);
        await new Promise((r) => setTimeout(r, 450));
        bot.setControlState("jump", false);
        await new Promise((r) => setTimeout(r, 250));
      }
      out({ ev: "jumped", bot: c.bot, n: c.jump });
    }
    if (c.walk) {
      bot.setControlState("forward", true);
      await new Promise((r) => setTimeout(r, c.walk * 1000));
      bot.setControlState("forward", false);
    }
    if (c.pos) out({ ev: "pos", bot: c.bot, pos: bot.entity.position });
    if (c.quit) bot.quit();
  } catch (e) {
    out({ ev: "error", bot: c.bot, error: String(e) });
  }
});
