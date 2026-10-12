import {expect,test} from "bun:test";
import {mkdtemp,rm,readFile,writeFile,symlink,mkdir} from "node:fs/promises";
import {join,resolve,sep} from "node:path";
import {tmpdir} from "node:os";
import {ScopedProtocolBlobs} from "../src/transport.ts";
import {canonicalJson} from "../src/protocol.ts";
import {INLINE_LIMIT_BYTES,type FileReference} from "../src/types.ts";

const scope={workflow_id:"wf",generation_id:"1",node_id:"coding"};
async function fixture<T>(body:(root:string,blobs:ScopedProtocolBlobs)=>Promise<T>):Promise<T> {
  const temp=resolve(tmpdir());const root=await mkdtemp(join(temp,"opencompany-pi-transport-"));
  try {return await body(root,await ScopedProtocolBlobs.create(root,scope,1));}
  finally {if(!resolve(root).startsWith(temp+sep))throw new Error("Invalid private transport test directory");await rm(root,{recursive:true,force:true});}
}
test("scoped transport keeps small values inline and verifies large canonical exact values",()=>fixture(async(root,blobs)=>{
  expect(await blobs.encode({text:"small"})).toEqual({text:"small"});
  const value={text:"x".repeat(INLINE_LIMIT_BYTES),id:9007199254740993n,"😀":2,"\uE000":1};
  const reference=await blobs.encode(value) as FileReference;
  expect(reference.pi_file.bytes).toBeGreaterThan(INLINE_LIMIT_BYTES);
  expect(await readFile(join(root,reference.pi_file.name),"utf8")).toBe(canonicalJson(value));
  expect(await blobs.decode(reference)).toEqual(value);
  expect(await blobs.encode(value)).toEqual(reference);
}));
test("the exact inline body limit is referenced so its LF cannot exceed the frame bound",()=>fixture(async(_root,blobs)=>{
  const value={text:"x".repeat(INLINE_LIMIT_BYTES-11)};
  expect(Buffer.byteLength(canonicalJson(value))).toBe(INLINE_LIMIT_BYTES);
  const reference=await blobs.encode(value) as FileReference;
  expect(reference.pi_file.bytes).toBe(INLINE_LIMIT_BYTES);
  expect(await blobs.decode(reference)).toEqual(value);
}));
test.each(["scope_digest","epoch","sha256","bytes","name"])("scoped transport rejects conflicting %s",field=>fixture(async(_root,blobs)=>{
  const reference=await blobs.encode({text:"x".repeat(INLINE_LIMIT_BYTES)}) as FileReference;
  const changed=structuredClone(reference);
  (changed.pi_file as unknown as Record<string,unknown>)[field]=field==="epoch"?2:field==="bytes"?42:"mismatch";
  await expect(blobs.decode(changed)).rejects.toMatchObject({code:"invalid_blob"});
}));
test("scoped transport rejects tampering and directory entries",()=>fixture(async(root,blobs)=>{
  const reference=await blobs.encode({text:"x".repeat(INLINE_LIMIT_BYTES)}) as FileReference;
  const path=join(root,reference.pi_file.name);
  await writeFile(path,"tampered");await expect(blobs.decode(reference)).rejects.toMatchObject({code:"invalid_blob"});
  await rm(path);await mkdir(path);await expect(blobs.decode(reference)).rejects.toMatchObject({code:"invalid_blob"});
}));
test("scoped transport refuses symlink files",()=>fixture(async(root,blobs)=>{
  const reference=await blobs.encode({text:"x".repeat(INLINE_LIMIT_BYTES)}) as FileReference;
  const path=join(root,reference.pi_file.name);const other=join(root,"other.json");
  await writeFile(other,await readFile(path));await rm(path);
  try {await symlink(other,path);} catch(error) {if((error as {code?:string}).code==="EPERM") return;throw error;}
  await expect(blobs.decode(reference)).rejects.toMatchObject({code:"invalid_blob"});
}));
