import { once } from "node:events";
import { canonicalDigest, FrameDecoder, encodeFrame } from "./protocol.ts";
import { DriverInterrupted, INLINE_LIMIT_BYTES, ProtocolError, type HostInput, type HostOutput, type OperationResult, type RunTurn } from "./types.ts";
import {ScopedProtocolBlobs} from "./transport.ts";

// Provider access belongs exclusively to native Temporal leaves. Disable SDK discovery
// and fail closed if an SDK change unexpectedly attempts a fetch inside this process.
process.env.PI_OFFLINE="1";
globalThis.fetch=(async()=>{throw new ProtocolError("network_disabled","Pi host network access is disabled");}) as unknown as typeof fetch;
const {TurnDriver}=await import("./driver.ts");
const decoder=new FrameDecoder(INLINE_LIMIT_BYTES);
const pending=new Map<string,{resolve:(value:OperationResult)=>void;reject:(error:Error)=>void;cleanup:()=>void}>();
let driver:InstanceType<typeof TurnDriver>|undefined;
let task:Promise<void>|undefined;
let terminal=false;
let closing=false;
let transportError:Error|undefined;
let transport:ScopedProtocolBlobs|undefined;
let lastInput=performance.now();
let outputChain=Promise.resolve();

function output(value:HostOutput):Promise<void> {
  // A rejected large checkpoint must not poison the queue that sends its
  // bounded terminal error. Each caller still receives its own write failure.
  outputChain=outputChain.catch(()=>{}).then(async()=>{
    let wire:unknown=value;
    if(value.type==="turn_checkpoint") {
      // Canonical assistant/tool bodies already appear in the snapshot and
      // workflow receipts. Do not send a second top-level body copy.
      const {assistant_message:_assistant,tool_results:_tools,...checkpoint}=value;
      wire=checkpoint;
    }
    let frame=encodeFrame(wire);
    if(Buffer.byteLength(frame,"utf8")>INLINE_LIMIT_BYTES) {
      if(!transport) throw new ProtocolError("missing_transport","Large Pi protocol values require a scoped transport directory");
      const reference=await transport.encode(wire);
      frame=encodeFrame({type:value.type,pi_payload:reference});
    }
    if (!process.stdout.write(frame)) await once(process.stdout,"drain");
  });
  return outputChain;
}
function interrupt(parked:boolean):void {
  if(terminal || closing) return;
  closing=true;
  driver?.interrupt(parked);
  for (const request of pending.values()) {request.cleanup();request.reject(new DriverInterrupted(parked));}
  pending.clear();
  // Before run_turn there is no task whose finally block can close the pipe.
  // The input iterator otherwise keeps an expired or terminated host alive.
  if(!task) process.stdin.destroy();
}
async function start(input:RunTurn):Promise<void> {
  if (driver || terminal || closing) throw new ProtocolError("duplicate_run","A Pi host accepts one round");
  driver=new TurnDriver(input,async(operation,signal)=>{
    if (pending.has(operation.id)) throw new ProtocolError("duplicate_operation","Pi issued a concurrent duplicate operation");
    const response=new Promise<OperationResult>((resolve,reject)=>{
      const aborted=()=>{pending.delete(operation.id);reject(new DriverInterrupted(false));};
      signal?.addEventListener("abort",aborted,{once:true});
      pending.set(operation.id,{resolve,reject,cleanup:()=>signal?.removeEventListener("abort",aborted)});
    });
    await output(operation);
    return await response;
  },output);
  task=(async()=>{
    try {const checkpoint=await driver!.execute();await output(checkpoint);terminal=true;}
    catch(error) {
      if (transportError) await output({type:"error",code:transportError instanceof ProtocolError ? transportError.code : "transport_failure",
        message:transportError instanceof ProtocolError ? transportError.message : "Pi host transport failed"});
      else if (error instanceof DriverInterrupted) await output(error.parked ? {type:"parked",turn:input.turn,epoch:input.epoch} : {type:"aborted"});
      else await output({type:"error",code:error instanceof ProtocolError ? error.code : "sdk_failure",
        message:error instanceof ProtocolError ? error.message : "Pi SDK round failed"});
      terminal=true;
    }
    finally {process.stdin.destroy();}
  })();
}
async function input(frame:HostInput):Promise<void> {
  lastInput=performance.now();
  if(frame.type==="run_turn" && !driver) {
    const bootstrap=frame as RunTurn;
    if(bootstrap.blob_dir) transport=await ScopedProtocolBlobs.create(bootstrap.blob_dir,bootstrap.scope,bootstrap.epoch);
  }
  if("pi_payload" in frame) {
    if(!transport) throw new ProtocolError("missing_transport","Pi protocol references require a scoped transport directory");
    const decoded=await transport.decode(frame.pi_payload) as HostInput;
    if(!decoded || typeof decoded!=="object" || decoded.type!==frame.type) throw new ProtocolError("invalid_blob","Pi protocol envelope type differs from its buffer");
    if(frame.type==="run_turn") {
      const run=decoded as RunTurn;
      const bootstrap=frame as unknown as RunTurn;
      if(run.epoch!==bootstrap.epoch || run.blob_dir!==bootstrap.blob_dir || canonicalDigest(run.scope)!==canonicalDigest(bootstrap.scope)) throw new ProtocolError("invalid_blob","Pi run buffer differs from its trusted bootstrap");
    }
    frame=decoded;
  }
  if("pi_payload" in frame) throw new ProtocolError("invalid_blob","Nested Pi protocol envelopes are not allowed");
  if (frame.type==="keepalive") return;
  if (frame.type==="abort" || frame.type==="park") {interrupt(frame.type==="park");return;}
  if (frame.type==="run_turn") {await start(frame);return;}
  if (frame.type==="operation_result") {
    const request=pending.get(frame.id);
    if (!request) throw new ProtocolError("unexpected_result","Operation result has no active request");
    pending.delete(frame.id);request.cleanup();request.resolve(frame.result);return;
  }
  throw new ProtocolError("unknown_frame","Unknown Pi protocol frame");
}
const watchdog=setInterval(()=>{if (!terminal && !closing && performance.now()-lastInput>120000) interrupt(false);},1000);
watchdog.unref();
process.on("SIGTERM",()=>{interrupt(false);});
process.on("SIGINT",()=>{interrupt(false);});

try {
  for await (const chunk of process.stdin) {
    for (const frame of decoder.push(chunk)) {if(terminal || closing) break;await input(frame);}
    if (terminal || closing) break;
  }
  decoder.end();
  if (!terminal && !closing) interrupt(false);
  await task;
} catch(error) {
  // destroy() can reject the input iterator. An intentional bootstrap shutdown
  // has no round to report and is already being reaped by this control path.
  if (!terminal && !closing) transportError=error instanceof Error ? error : new ProtocolError("transport_failure","Pi host transport failed");
  interrupt(false);
  await task;
  if (!terminal && transportError) await output({type:"error",code:error instanceof ProtocolError ? error.code : "transport_failure",
    message:error instanceof ProtocolError ? error.message : "Pi host transport failed"});
  if (transportError) process.exitCode=1;
} finally {
  clearInterval(watchdog);
  for (const request of pending.values()) {request.cleanup();request.reject(new DriverInterrupted(false));}
  pending.clear();
  await outputChain;
  // Closing stdin lets Python await process completion immediately after a terminal frame.
  process.stdin.destroy();
}
