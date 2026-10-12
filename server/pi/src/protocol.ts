import { createHash } from "node:crypto";
import { MAX_FRAME_BYTES, ProtocolError, type HostInput } from "./types.ts";

/** Recursively sorted UTF-8 JSON; shared with the Python coordinator. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort(compareUnicodeKeys).map(key => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  throw new ProtocolError("invalid_json", "Protocol values must be finite JSON values");
}
/** Python sorts Unicode scalar values, whereas JavaScript's default sort uses UTF-16. */
function compareUnicodeKeys(left:string,right:string):number {
  const a=Array.from(left),b=Array.from(right);
  for(let index=0;index<Math.min(a.length,b.length);index++) {
    const difference=a[index]!.codePointAt(0)!-b[index]!.codePointAt(0)!;
    if(difference) return difference;
  }
  return a.length-b.length;
}
/** Bun 1.4 exposes the original numeric token to JSON revivers. */
export function losslessParse(text:string):unknown {
  const parse=JSON.parse as (source:string,reviver:(key:string,value:unknown,context:{source?:string})=>unknown)=>unknown;
  return parse(text,(_key,value,context)=>{
    if(typeof value==="number" && !Number.isSafeInteger(value) && context.source && /^-?[0-9]+$/.test(context.source)) return BigInt(context.source);
    return value;
  });
}
export function canonicalDigest(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

/** Split on LF bytes only, including when UTF-8 characters span pipe chunks. */
export class FrameDecoder {
  private buffered = Buffer.alloc(0);
  constructor(private readonly limit = MAX_FRAME_BYTES) {}
  push(chunk: Uint8Array): HostInput[] {
    this.buffered = Buffer.concat([this.buffered, chunk]);
    const frames: HostInput[] = [];
    let boundary: number;
    while ((boundary = this.buffered.indexOf(0x0a)) >= 0) {
      if (boundary > this.limit) throw new ProtocolError("frame_too_large", "Protocol frame exceeds its limit");
      const raw = this.buffered.subarray(0, boundary);
      this.buffered = this.buffered.subarray(boundary + 1);
      if (!raw.length) throw new ProtocolError("invalid_frame", "Empty protocol frame");
      let parsed: unknown;
      try { parsed = losslessParse(new TextDecoder("utf-8", { fatal: true }).decode(raw)); }
      catch { throw new ProtocolError("invalid_frame", "Protocol frame must contain valid UTF-8 JSON"); }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || typeof (parsed as {type?: unknown}).type !== "string") {
        throw new ProtocolError("invalid_frame", "Protocol frame requires a type");
      }
      canonicalJson(parsed);
      frames.push(parsed as HostInput);
    }
    if (this.buffered.length > this.limit) throw new ProtocolError("frame_too_large", "Protocol frame exceeds its limit");
    return frames;
  }
  end(): void {
    if (this.buffered.length) throw new ProtocolError("incomplete_frame", "EOF interrupted a protocol frame");
  }
}
export function encodeFrame(value: unknown): string {
  const rawJson=(JSON as unknown as {rawJSON:(source:string)=>unknown}).rawJSON;
  const encoded = JSON.stringify(value,(_key,item)=>typeof item==="bigint" ? rawJson(item.toString()) : item);
  if (Buffer.byteLength(encoded, "utf8") > MAX_FRAME_BYTES) throw new ProtocolError("frame_too_large", "Protocol frame exceeds its limit");
  return `${encoded}\n`;
}
