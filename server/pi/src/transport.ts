import {constants} from "node:fs";
import {lstat,open,realpath} from "node:fs/promises";
import {isAbsolute,join,resolve,sep} from "node:path";
import {createHash} from "node:crypto";
import {canonicalDigest,canonicalJson,encodeFrame,losslessParse} from "./protocol.ts";
import {INLINE_LIMIT_BYTES,MAX_FRAME_BYTES,ProtocolError,type FileReference,type Scope} from "./types.ts";

/** Disposable, private transport buffers. Root/scope/epoch come only from Python. */
export class ScopedProtocolBlobs {
  private constructor(private readonly root:string,private readonly scopeDigest:string,private readonly epoch:number) {}
  static async create(root:string,scope:Scope,epoch:number):Promise<ScopedProtocolBlobs> {
    if(typeof root!=="string" || !isAbsolute(root) || !Number.isSafeInteger(epoch) || epoch<0) throw new ProtocolError("invalid_transport","Pi transport requires an absolute trusted directory and epoch");
    const absolute=resolve(root);
    try {
      const stat=await lstat(absolute);
      if(!stat.isDirectory() || stat.isSymbolicLink() || await realpath(absolute)!==absolute) throw new ProtocolError("invalid_transport","Pi transport directory must be a canonical private directory");
    } catch(error) {
      if(error instanceof ProtocolError) throw error;
      throw new ProtocolError("invalid_transport","Pi transport directory is unavailable");
    }
    return new ScopedProtocolBlobs(absolute,canonicalDigest(scope),epoch);
  }
  async encode(value:unknown):Promise<unknown|FileReference> {
    // SDK entries can have optional undefined properties, which ordinary JSON
    // omits. Preserve unsafe integer tokens while normalizing those properties.
    const raw=Buffer.from(canonicalJson(losslessParse(encodeFrame(value))),"utf8");
    if(raw.length<INLINE_LIMIT_BYTES) return value;
    if(raw.length>MAX_FRAME_BYTES) throw new ProtocolError("blob_too_large","Pi transport value exceeds its bounded blob limit");
    const sha256=createHash("sha256").update(raw).digest("hex");
    const reference:FileReference={pi_file:{name:`${sha256}.json`,sha256,bytes:raw.length,scope_digest:this.scopeDigest,epoch:this.epoch}};
    const path=this.path(reference.pi_file.name);
    try {
      const output=await open(path,"wx",0o600);
      try {await output.writeFile(raw);} finally {await output.close();}
    } catch(error) {
      if((error as {code?:string}).code!=="EEXIST") throw new ProtocolError("transport_write_failed","Pi transport buffer could not be written");
      await this.decode(reference);
    }
    return reference;
  }
  async decode(value:unknown):Promise<unknown> {
    if(!value || typeof value!=="object" || !("pi_file" in value)) return value;
    const outer=value as {pi_file:unknown};
    if(Object.keys(outer).length!==1 || !outer.pi_file || typeof outer.pi_file!=="object" || Array.isArray(outer.pi_file)) throw new ProtocolError("invalid_blob","Malformed Pi transport reference");
    const ref=outer.pi_file as FileReference["pi_file"];
    if(Object.keys(ref).sort().join(",")!=="bytes,epoch,name,scope_digest,sha256" || !/^[a-f0-9]{64}$/.test(ref.sha256)
      || ref.name!==`${ref.sha256}.json` || ref.scope_digest!==this.scopeDigest || ref.epoch!==this.epoch
      || !Number.isSafeInteger(ref.bytes) || ref.bytes<INLINE_LIMIT_BYTES || ref.bytes>MAX_FRAME_BYTES) throw new ProtocolError("invalid_blob","Pi transport reference does not match its admitted scope");
    const path=this.path(ref.name);
    let raw:Buffer;
    try {
      const stat=await lstat(path);
      if(!stat.isFile() || stat.isSymbolicLink() || await realpath(path)!==path || stat.size!==ref.bytes) throw new ProtocolError("invalid_blob","Pi transport buffer must be a scoped regular file");
      const input=await open(path,constants.O_RDONLY|(constants.O_NOFOLLOW ?? 0));
      try {
        const opened=await input.stat();
        if(!opened.isFile() || opened.size!==ref.bytes) throw new ProtocolError("invalid_blob","Pi transport buffer must be a scoped regular file");
        raw=Buffer.alloc(ref.bytes+1);
        let offset=0;
        while(offset<raw.length) {
          const next=await input.read(raw,offset,raw.length-offset,null);
          if(!next.bytesRead) break;
          offset+=next.bytesRead;
        }
        raw=raw.subarray(0,offset);
      } finally {await input.close();}
    } catch(error) {
      if(error instanceof ProtocolError) throw error;
      throw new ProtocolError("invalid_blob","Pi transport buffer is unavailable");
    }
    if(raw.length!==ref.bytes || createHash("sha256").update(raw).digest("hex")!==ref.sha256) throw new ProtocolError("invalid_blob","Pi transport buffer failed integrity verification");
    try {
      const decoded=losslessParse(new TextDecoder("utf-8",{fatal:true}).decode(raw));
      if(canonicalJson(decoded)!==raw.toString("utf8")) throw new ProtocolError("invalid_blob","Pi transport buffer is not canonical JSON");
      return decoded;
    } catch(error) {
      if(error instanceof ProtocolError) throw error;
      throw new ProtocolError("invalid_blob","Pi transport buffer contains invalid JSON");
    }
  }
  private path(name:string):string {
    const path=resolve(join(this.root,name));
    if(!path.startsWith(this.root+sep)) throw new ProtocolError("invalid_blob","Pi transport buffer escapes its admitted directory");
    return path;
  }
}
