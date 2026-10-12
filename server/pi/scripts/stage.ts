import {createHash} from "node:crypto";
import {mkdir,copyFile,readFile} from "node:fs/promises";
import {dirname,join,resolve} from "node:path";
import {fileURLToPath} from "node:url";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const index=process.argv.indexOf("--out");
if (index<0 || !process.argv[index+1]) throw new Error("Usage: bun run scripts/stage.ts --out <server/pi directory>");
const output=resolve(process.argv[index+1]!);
const dist=join(root,"dist");
const manifest=JSON.parse(await readFile(join(dist,"runtime-manifest.json"),"utf8"));
const archive=await readFile(join(dist,"runtime.tgz"));
if (manifest.sdk_version!=="1.1.0" || manifest.protocol!==1 || manifest.sha256!==createHash("sha256").update(archive).digest("hex")) {
  throw new Error("Pi runtime archive verification failed; rebuild it before staging");
}
await mkdir(join(output,"dist"),{recursive:true});
for (const file of ["index.js","runtime.tgz","runtime-manifest.json"]) await copyFile(join(dist,file),join(output,"dist",file));
console.log(`Staged pinned Pi runtime in ${output}`);
