import { canonicalDigest, canonicalJson } from "../src/protocol.ts";
import type { MessageWire, RunTurn, ToolCallWire } from "../src/types.ts";

export function message(role: string, content = "", calls: ToolCallWire[] = []): MessageWire {
  return {role, content, blocks:[...(content ? [{type:role==="tool"?"tool_result":"text",text:content,tool_call:null,tool_call_id:null,name:null,metadata:{},source:null}] : []),
    ...calls.map(call=>({type:"tool_call",text:"",tool_call:call,tool_call_id:null,name:null,metadata:{},source:null}))],
    tool_calls:calls,tool_call_id:null,name:null,provider_state:{}};
}
export function call(id = "c1", name = "file_read", args: Record<string,unknown> = {file_path:"a.txt"}): ToolCallWire {
  return {id,name,args,raw_arguments:canonicalJson(args),parse_error:null};
}
export function toolResult(id = "c1", name = "file_read", content = "ok"): MessageWire {
  return {...message("tool", content),tool_call_id:id,name};
}
export function run(messages = [message("system", "Keep the canonical policy."), message("user", "Read a.txt")]): RunTurn {
  return {type:"run_turn",protocol:1,turn:0,epoch:1,scope:{workflow_id:"wf",generation_id:"1",node_id:"coding"},
    cwd:process.cwd(),model:{id:"native",provider:"anthropic"},messages,context_digest:canonicalDigest(messages),
    config_digest:canonicalDigest({model:"native"}),binding_digest:canonicalDigest([]),
    tools:[{name:"file_read",description:"Read a workspace file",parameters:{type:"object",properties:{file_path:{type:"string"}},required:["file_path"],additionalProperties:false}}]};
}
