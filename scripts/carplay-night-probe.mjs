// Ask the live CarPlay session for day then night appearance and save one decoded keyframe of each.
// Usage (repo root, Node 22+, machine on the F50 LAN, ffmpeg in PATH):
//   NODE_TLS_REJECT_UNAUTHORIZED=0 node scripts/carplay-night-probe.mjs [https://192.168.77.1:18443] [OUT_DIR] [--frame-only]
// --frame-only saves the current screen as frame.png without sending any day/night request.
// --tap=X,Y (0-1 of the CarPlay screen) taps once before a --frame-only capture.
// TLS check is disabled only for this local self-signed receiver. Ends by requesting day mode again;
// an open viewer re-sends its own setting on its next session or schedule change.
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';

const base = process.argv[2] ?? 'https://192.168.77.1:18443';
const out = process.argv[3] ?? 'artifacts/night-probe';
mkdirSync(out, { recursive: true });
const access = await (await fetch(`${base}/receiver-access.js`)).text();
const token = access.match(/"([0-9a-f]{32})"/)?.[1];
if (!token) throw new Error('LAN viewer access not available');

let config = null, waiting = null;
const ws = new WebSocket(`${base.replace('https', 'wss')}/ws?token=${token}`);
ws.binaryType = 'arraybuffer';
ws.onmessage = ({ data }) => {
  if (typeof data === 'string') return;
  const b = new Uint8Array(data), v = new DataView(data);
  if (b[0] === 1) config = { width: v.getUint32(3, true), height: v.getUint32(7, true), avcc: b.slice(11) };
  if (b[0] === 2 && (b[2] & 1) && config && waiting) { const done = waiting; waiting = null; done(b.slice(11)); }
};
await new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = fail; });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const send = m => ws.send(JSON.stringify(m));

function annexB(avcc) { // SPS/PPS from avcC with start codes
  const parts = []; let i = 5; const sps = avcc[i++] & 31;
  for (let n = 0; n < sps; n++) { const l = (avcc[i] << 8) | avcc[i + 1]; parts.push(avcc.slice(i + 2, i + 2 + l)); i += 2 + l; }
  const pps = avcc[i++];
  for (let n = 0; n < pps; n++) { const l = (avcc[i] << 8) | avcc[i + 1]; parts.push(avcc.slice(i + 2, i + 2 + l)); i += 2 + l; }
  return Buffer.concat(parts.flatMap(p => [Buffer.from([0, 0, 0, 1]), Buffer.from(p)]));
}
async function capture(name, night) {
  if (night !== null) { send({ type: 'night', night }); await sleep(4000); }
  const frame = await Promise.race([new Promise(r => { waiting = r; send({ type: 'keyframe' }); }), sleep(8000).then(() => null)]);
  if (!frame || !config) throw new Error(`no keyframe for ${name}`);
  const raw = `${out}/${name}.h264`, png = `${out}/${name}.png`;
  writeFileSync(raw, Buffer.concat([annexB(config.avcc), Buffer.from(frame)]));
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', raw, '-frames:v', '1', png]);
  // Mean luma (0-255) of the whole frame: dark CarPlay appearance is clearly lower.
  return execFileSync('ffmpeg', ['-v', 'error', '-i', png, '-vf', 'scale=1:1:flags=area,format=gray', '-f', 'rawvideo', '-'])[0];
}
await sleep(1500);
const frameOnly = process.argv.includes('--frame-only');
const tap = process.argv.find(a => a.startsWith('--tap='))?.slice(6).split(',').map(Number);
if (tap) {
  for (const down of [true, false]) { send({ type: 'touch', contacts: [{ id: 0, x: tap[0], y: tap[1], down }] }); await sleep(120); }
  await sleep(2000);
}
for (const [name, night] of frameOnly ? [['frame', null]] : [['day', false], ['night', true]]) {
  let luma;
  try { luma = await capture(name, night); } catch (e) { console.log(name, 'failed:', e.message); continue; }
  console.log(name, `${config.width}x${config.height}`, `${out}/${name}.png`, `mean luma ${luma}`);
}
if (!frameOnly) send({ type: 'night', night: false });
await sleep(500);
ws.close();
