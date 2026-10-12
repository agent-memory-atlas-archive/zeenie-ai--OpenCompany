import {expect,test} from "bun:test";
import {createHash} from "node:crypto";
import {readFile,mkdir,mkdtemp,realpath,rm} from "node:fs/promises";
import {dirname,join,resolve,sep} from "node:path";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import {smokeHost} from "./process.test.ts";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const manifestPath=join(root,"dist","runtime-manifest.json");
const distributionTest=await Bun.file(manifestPath).exists() ? test : test.skip;
distributionTest("production archive runs offline without source or root dependency resolution",async()=>{
  const manifest=JSON.parse(await readFile(manifestPath,"utf8"));
  const archive=join(root,"dist",manifest.archive);
  const bytes=await readFile(archive);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(manifest.sha256);
  expect(manifest.sdk_version).toBe("1.1.0");
  expect(manifest.universal).toBe(true);
  // Outside the repository so missing runtime packages cannot resolve from
  // server/pi/node_modules, root node_modules or any development workspace.
  const buildRoot=await realpath(tmpdir());
  const isolated=await realpath(await mkdtemp(join(buildRoot,"opencompany-pi-offline-")));
  try {
    const unpack=Bun.spawn(["tar","-xzf",archive,"-C",isolated],{stdout:"pipe",stderr:"pipe"});
    expect(await unpack.exited).toBe(0);
    for (const platform of ["darwin-arm64","darwin-x64","linux-x64","win32-x64"]) {
      const binary=join(isolated,"node_modules","@esbuild",platform,platform.startsWith("win32-") ? "esbuild.exe" : "bin/esbuild");
      expect(await Bun.file(binary).exists()).toBe(true);
    }
    await smokeHost(join(isolated,manifest.entrypoint),{NODE_PATH:"",PI_PACKAGE_DIR:join(isolated,manifest.sdk_package_dir),PI_OFFLINE:"1"});
  } finally {
    if (!resolve(isolated).startsWith(resolve(buildRoot)+sep)) throw new Error("Invalid offline test scratch path");
    await rm(isolated,{recursive:true,force:true});
  }
},30000);
