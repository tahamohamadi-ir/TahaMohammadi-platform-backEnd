import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..", "..", "..", "..", "..");
const sourceDir = resolve(repoRoot, "apps", "web", "src", "data");
const outputPath = resolve(__dirname, "profile.seed.json");

function loadProfileObject(filename) {
  const sourcePath = resolve(sourceDir, filename);
  const source = readFileSync(sourcePath, "utf8")
    .replace(/^import .*?;\s*/m, "")
    .replace(/export const \w+: Profile = /, "module.exports = ");

  const context = { module: { exports: {} } };
  vm.runInNewContext(source, context, { filename: sourcePath });
  return context.module.exports;
}

const profileEn = loadProfileObject("profile.en.ts");
const profileFa = loadProfileObject("profile.fa.ts");

const payload = {
  source: [
    "apps/web/src/data/profile.ts",
    "apps/web/src/data/profile.en.ts",
    "apps/web/src/data/profile.fa.ts",
  ],
  translationKey: "0d6e5b42-b8e1-45f2-88b1-a89a7b6f2f23",
  profiles: {
    en: profileEn,
    fa: profileFa,
  },
};

writeFileSync(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(`Wrote ${outputPath}`);
