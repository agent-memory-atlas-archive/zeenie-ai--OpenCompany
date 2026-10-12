import {expect,test} from "bun:test";
import {canonicalDigest,canonicalJson,encodeFrame,FrameDecoder,losslessParse} from "../src/protocol.ts";

test("LF framing preserves unicode separators and split UTF-8",()=>{
  const text=encodeFrame({type:"operation_result",id:"🧪",result:{text:"a\u2028b\u2029c"}});
  const bytes=Buffer.from(text);const decoder=new FrameDecoder();
  const frames=[];for (const byte of bytes) frames.push(...decoder.push(Uint8Array.of(byte)));
  decoder.end();expect(frames).toHaveLength(1);expect((frames[0] as unknown as {result:{text:string}}).result.text).toBe("a\u2028b\u2029c");
});
test("unsafe integer tokens round trip as exact native JSON numbers",()=>{
  const input='{"type":"operation_result","id":"op","result":{"id":9007199254740993,"neg":-9007199254740993}}\n';
  const decoder=new FrameDecoder();const parsed=decoder.push(Buffer.from(input))[0]!;
  expect((parsed as unknown as {result:{id:bigint}}).result.id).toBe(9007199254740993n);
  expect(encodeFrame(parsed)).toBe(input);
  expect(canonicalJson(losslessParse('{"z":9007199254740993,"a":1e21}'))).toBe('{"a":1e+21,"z":9007199254740993}');
});
test("object key ordering follows Unicode scalar values across the astral boundary",()=>{
  expect(canonicalJson({"😀":2,"\uE000":1})).toBe('{"\uE000":1,"😀":2}');
});
test("bounded framing rejects malformed, oversized and incomplete data",()=>{
  expect(()=>new FrameDecoder(4).push(Buffer.from("12345"))).toThrow("exceeds");
  expect(()=>new FrameDecoder().push(Buffer.from("not-json\n"))).toThrow("UTF-8 JSON");
  expect(()=>new FrameDecoder().push(Uint8Array.of(0xff,0x0a))).toThrow("UTF-8 JSON");
  const incomplete=new FrameDecoder();incomplete.push(Buffer.from('{"type":"park"}'));expect(()=>incomplete.end()).toThrow("EOF");
});
test("sorted unicode JSON fingerprint fixtures match Python number spelling",()=>{
  expect(canonicalJson({z:1.0,a:[-0.0,1e-7,1e-6,1e21,"🧪"]})).toBe('{"a":[0,1e-7,0.000001,1e+21,"🧪"],"z":1}');
  expect(canonicalDigest({z:2,a:"π"})).toBe("a24fe78c0a1c14cf9d1bf2309ad792a20df22a9eabbda4ea9e91b59bba809be1");
});
