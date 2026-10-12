import {expect,test} from "bun:test";
import {join,dirname,resolve,sep} from "node:path";
import {fileURLToPath} from "node:url";
import {encodeFrame,losslessParse,canonicalDigest} from "../src/protocol.ts";
import {ScopedProtocolBlobs} from "../src/transport.ts";
import {INLINE_LIMIT_BYTES} from "../src/types.ts";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {message,run,toolResult,call} from "./fixtures.ts";

const source=join(dirname(fileURLToPath(import.meta.url)),"..","src","index.ts");
export async function smokeHost(entrypoint:string,env:Record<string,string|undefined>={}):Promise<void> {
  const child=Bun.spawn([process.execPath,entrypoint],{stdin:"pipe",stdout:"pipe",stderr:"pipe",env:{...process.env,...env}});
  child.stdin.write(encodeFrame(run()));
  let buffer="";let ended=false;const observed:string[]=[];
  for await(const chunk of child.stdout) {
    buffer+=new TextDecoder().decode(chunk);
    let lf:number;
    while((lf=buffer.indexOf("\n"))>=0) {
      const event=JSON.parse(buffer.slice(0,lf));buffer=buffer.slice(lf+1);observed.push(event.type);
      if(event.type==="operation") child.stdin.write(encodeFrame({type:"operation_result",id:event.id,
        result:event.kind==="model"?{assistant_message:message("assistant","read",[call()])}:{message:toolResult()}}));
      if(event.type==="turn_checkpoint") {expect(event.snapshot.entries.some((entry:{type:string;message?:{role:string}})=>entry.type==="message" && entry.message?.role==="toolResult")).toBe(true);ended=true;}
      if(event.type==="error") throw new Error(`${event.code}: ${event.message}`);
    }
  }
  expect(ended).toBe(true);expect(observed).toEqual(["operation","assistant_ready","operation","turn_checkpoint"]);
  expect(await child.exited).toBe(0);expect(await new Response(child.stderr).text()).toBe("");
}
test("isolated JSONL host completes and exits without stdin EOF",()=>smokeHost(source),15000);
test("stdin EOF aborts and reaps a waiting SDK host",async()=>{
  const child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
  child.stdin.write(encodeFrame(run()));
  const reader=child.stdout.getReader();const first=await reader.read();expect(new TextDecoder().decode(first.value)).toContain('"type":"operation"');
  child.stdin.end();
  let rest="";for(;;){const next=await reader.read();if(next.done)break;rest+=new TextDecoder().decode(next.value);}
  expect(rest).toContain('"type":"aborted"');expect(await child.exited).toBe(0);
},15000);
test("park control returns successful maintenance completion",async()=>{
  const child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
  child.stdin.write(encodeFrame(run()));
  const reader=child.stdout.getReader();await reader.read();child.stdin.write(encodeFrame({type:"park"}));
  let rest="";for(;;){const next=await reader.read();if(next.done)break;rest+=new TextDecoder().decode(next.value);}
  expect(rest).toContain('"type":"parked"');expect(rest).not.toContain("turn_checkpoint");expect(await child.exited).toBe(0);
},15000);

test("large run, receipts and checkpoint use scoped refs within the inline frame bound",async()=>{
  const temp=tmpdir();const root=await mkdtemp(join(temp,"opencompany-pi-large-round-"));
  let child:ReturnType<typeof Bun.spawn>|undefined;
  try {
    const input=run();input.blob_dir=root;input.messages[1]!.provider_state={large:"u".repeat(INLINE_LIMIT_BYTES),id:9007199254740993n};
    input.context_digest=canonicalDigest(input.messages);
    const blobs=await ScopedProtocolBlobs.create(root,input.scope,input.epoch);
    const encoded=await blobs.encode(input);
    const frame=encodeFrame({type:"run_turn",protocol:1,scope:input.scope,epoch:input.epoch,blob_dir:root,pi_payload:encoded});
    expect(Buffer.byteLength(frame)).toBeLessThan(INLINE_LIMIT_BYTES);
    child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
    (child.stdin as Bun.FileSink).write(frame);let buffer="";let checkpointSeen=false;let refSeen=false;
    for await(const chunk of child.stdout as ReadableStream<Uint8Array>) {
      buffer+=new TextDecoder().decode(chunk);let lf:number;
      while((lf=buffer.indexOf("\n"))>=0) {
        const raw=buffer.slice(0,lf);buffer=buffer.slice(lf+1);expect(Buffer.byteLength(raw)).toBeLessThan(INLINE_LIMIT_BYTES);
        let event=losslessParse(raw) as Record<string,unknown>;
        if(event.pi_payload) {refSeen=true;event=await blobs.decode(event.pi_payload) as Record<string,unknown>;}
        if(event.type==="operation") {
          const response={type:"operation_result",id:event.id,result:{assistant_message:message("assistant","done")}};
          (response.result.assistant_message.provider_state as Record<string,unknown>).large="a".repeat(INLINE_LIMIT_BYTES);
          const reference=await blobs.encode(response);
          (child.stdin as Bun.FileSink).write(encodeFrame({type:"operation_result",pi_payload:reference}));
        }
        if(event.type==="turn_checkpoint") {checkpointSeen=true;expect(event).not.toHaveProperty("assistant_message");expect(event).not.toHaveProperty("tool_results");}
        if(event.type==="error") throw new Error(`${event.code}: ${event.message}`);
      }
    }
    expect(checkpointSeen).toBe(true);expect(refSeen).toBe(true);expect(await child.exited).toBe(0);
  } finally {
    if(child && child.exitCode===null)child.kill();
    const absolute=resolve(root);
    if(!absolute.startsWith(resolve(temp)+sep))throw new Error("Transport test cleanup path escaped its temporary directory");
    await rm(absolute,{recursive:true,force:true});
  }
},15000);
