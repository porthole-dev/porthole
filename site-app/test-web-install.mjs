import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';
import { BlobReader, BlobWriter, TextReader, Uint8ArrayReader, ZipWriter } from '@zip.js/zip.js';
import { digestBlob, inspectSparse, prepareBundle, installImages, bindDevice } from './src/web-install.js';
import { FastbootDevice } from 'android-fastboot/dist/fastboot.mjs';

function sparseImage(rawChunks = 3, blocksPerChunk = 2) {
  const chunks = [];
  for (let n = 0; n < rawChunks; n++) {
    const data = new Uint8Array(12 + blocksPerChunk * 4096).fill(n + 1);
    const view = new DataView(data.buffer);
    view.setUint16(0, 0xcac1, true); view.setUint16(2, 0, true);
    view.setUint32(4, blocksPerChunk, true); view.setUint32(8, data.length, true);
    chunks.push(data);
  }
  const header = new Uint8Array(28), view = new DataView(header.buffer);
  view.setUint32(0, 0xed26ff3a, true); view.setUint16(4, 1, true);
  view.setUint16(8, 28, true); view.setUint16(10, 12, true); view.setUint32(12, 4096, true);
  view.setUint32(16, rawChunks * blocksPerChunk, true); view.setUint32(20, rawChunks, true);
  return new Blob([header, ...chunks]);
}
const rootfs = sparseImage();
const root = gzipSync(new Uint8Array(await rootfs.arrayBuffer()));
const boot = new Blob(['boot']), dtbo = new Blob(['overlay']);
const manifest = { device: 'google-taimen', product: 'taimen', slot: 'b', dtbo: true, sha256: {
  'google-taimen.img.gz': await digestBlob(new Blob([root])),
  'boot.img': await digestBlob(boot), 'dtbo.img': await digestBlob(dtbo),
} };
async function bundle(overrides = {}) {
  const writer = new BlobWriter(), zip = new ZipWriter(writer);
  for (const [name, value] of Object.entries({ 'bundle.json': JSON.stringify(manifest), 'google-taimen.img.gz': root, 'boot.img': new Uint8Array(await boot.arrayBuffer()), 'dtbo.img': new Uint8Array(await dtbo.arrayBuffer()), ...overrides })) {
    await zip.add(name, typeof value === 'string' ? new TextReader(value) : new Uint8ArrayReader(value), { level: 0 });
  }
  await zip.close(); return writer.getData();
}
const archive = await bundle();
const release = { device: manifest.device, size: archive.size, sha256: await digestBlob(archive), dtbo_sha256: manifest.sha256['dtbo.img'] };
const stored = new Map();
const storage = {
  async getFileHandle(name) {
    return { async createWritable() {
      const chunks = [];
      return new WritableStream({ write(data) { chunks.push(data); }, close() { stored.set(name, new Blob(chunks)); } });
    }, async getFile() { return stored.get(name); } };
  }, async removeEntry(name) { stored.delete(name); },
};
const prepared = await prepareBundle(archive, release, storage, 'porthole-a.img');
assert.deepEqual(new Uint8Array(await prepared.rootfs.arrayBuffer()), new Uint8Array(await rootfs.arrayBuffer()));
assert.equal(prepared.sparse.largest, 8192);
await assert.rejects(prepareBundle(archive, { ...release, sha256: '0'.repeat(64) }, storage, 'porthole-b.img'), /checksum mismatch/);
const corrupt = await bundle({ 'boot.img': new Uint8Array([42]) });
await assert.rejects(prepareBundle(corrupt, { ...release, size: corrupt.size, sha256: await digestBlob(corrupt) }, storage, 'porthole-c.img'), /Image checksum mismatch/);
await assert.rejects(inspectSparse(new Blob(['not sparse'])), /Unsupported rootfs/);
const truncated = rootfs.slice(0, rootfs.size - 1);
await assert.rejects(inspectSparse(truncated), /Truncated|Invalid sparse/);

function fake(values = {}, failure = '') {
  const operations = [];
  return { operations, async getVariable(name) { return ({ product: 'taimen', unlocked: 'yes', 'max-download-size': '0x10000000', 'partition-size:userdata': '0x100000000', ...values })[name]; },
    async flashBlob(name) { operations.push('flash:' + name); if (name === failure) throw new Error('write failed'); },
    async runCommand(command) { operations.push(command); },
  };
}
let driver = fake();
await installImages(driver, prepared, 'google-taimen');
assert.deepEqual(driver.operations, ['flash:userdata', 'flash:boot_b', 'flash:dtbo_b', 'set_active:b', 'reboot']);
for (const values of [{ product: 'walleye' }, { unlocked: 'no' }, { 'max-download-size': null }, { 'max-download-size': '0' }, { 'partition-size:userdata': '1' }]) {
  driver = fake(values); await assert.rejects(installImages(driver, prepared, 'google-taimen')); assert.deepEqual(driver.operations, []);
}
driver = fake(); await assert.rejects(installImages(driver, prepared, 'cancel')); assert.deepEqual(driver.operations, []);
driver = fake({}, 'boot_b'); await assert.rejects(installImages(driver, prepared, 'google-taimen')); assert.deepEqual(driver.operations, ['flash:userdata', 'flash:boot_b']);

// Exercise the actual pinned library's sparse splitting and reconstruct each write.
globalThis.FileReader = class {
  readAsArrayBuffer(blob) { blob.arrayBuffer().then(value => { this.result = value; this.onload(); }).catch(error => { this.error = error; this.onerror(); }); }
};
const client = new FastbootDevice(), payloads = [];
client.getVariable = async () => null; client._getDownloadSize = async () => 12000;
client.upload = async (_, data) => { assert(data.byteLength <= 12032); payloads.push(new Blob([data])); };
client.runCommand = async command => { assert.equal(command, 'flash:userdata'); };
await client.flashBlob('userdata', rootfs);
assert.equal(payloads.length, 3);
const reconstructed = new Uint8Array(6 * 4096);
for (const blob of payloads) {
  await inspectSparse(blob);
  const data = new Uint8Array(await blob.arrayBuffer()), view = new DataView(data.buffer);
  let offset = 28, block = 0;
  for (let n = 0; n < view.getUint32(20, true); n++) {
    const type = view.getUint16(offset, true), count = view.getUint32(offset + 4, true), size = view.getUint32(offset + 8, true);
    if (type === 0xcac1) reconstructed.set(data.slice(offset + 12, offset + size), block * 4096);
    block += count; offset += size;
  }
}
for (let n = 0; n < 3; n++) assert(reconstructed.slice(n * 8192, (n + 1) * 8192).every(value => value === n + 1));

// Bind without the library's automatic reconnect handler; changes cannot redirect writes.
const listeners = new Map();
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { usb: { addEventListener(name, handler) { listeners.set(name, handler); }, removeEventListener(name) { listeners.delete(name); } } } });
const selected = { 'serialNumber': 'fixture', opened: false,
  configurations: [{ interfaces: [{ alternates: [{ endpoints: [{ type: 'bulk', direction: 'in', endpointNumber: 1 }, { type: 'bulk', direction: 'out', endpointNumber: 2 }] }] }] }],
  async open() { this.opened = true; }, async reset() {}, async selectConfiguration() {}, async claimInterface() {}, async close() { this.opened = false; },
  async transferOut() {}, async transferIn() { return { data: new DataView(new TextEncoder().encode('OKAY').buffer) }; },
};
const bound = await bindDevice(selected, manifest);
assert(!listeners.has('connect'));
await assert.rejects(bound.driver.runCommand('flashing unlock'), /Unexpected/);
await bound.driver.runCommand('getvar:product');
listeners.get('disconnect')({ device: selected });
await assert.rejects(bound.driver.runCommand('flash:userdata'), /disconnected/);
await bound.close(); await prepared.dispose(); assert.equal(stored.size, 0);
console.log('web installer: verified ZIP, streamed rootfs, checksum refusals, model/unlock/size checks, confirmation, failed-write stop, actual sparse splitting and USB identity guard passed');
