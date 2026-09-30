import { createSHA256 } from 'hash-wasm';
import { BlobReader, BlobWriter, TextWriter, ZipReader, configure } from '@zip.js/zip.js';
import { FastbootDevice } from 'android-fastboot/dist/fastboot.mjs';

configure({ useWebWorkers: false });
const MiB = 1024 * 1024;
export const maxRootfs = 8 * 1024 * MiB;

export async function digestBlob(blob, progress = () => {}) {
  const hash = await createSHA256();
  hash.init();
  const reader = blob.stream().getReader();
  let read = 0, announced = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      hash.update(value);
      read += value.byteLength;
      if (read - announced >= 16 * MiB) { progress(read / blob.size); announced = read; }
    }
  } finally { reader.releaseLock(); }
  progress(1);
  return hash.digest();
}

export function validateManifest(manifest, release) {
  const device = release.device;
  if (!/^[a-z0-9-]+$/.test(device) || manifest.device !== device ||
      !/^[a-z0-9_-]+$/.test(manifest.product) || !['a', 'b'].includes(manifest.slot) ||
      typeof manifest.dtbo !== 'boolean' || !manifest.sha256) throw new Error('Invalid reviewed device policy.');
  const required = [device + '.img.gz', 'boot.img'];
  if (release.dtbo_sha256) {
    required.push('dtbo.img');
    if (!manifest.dtbo || manifest.sha256['dtbo.img'] !== release.dtbo_sha256) throw new Error('Wrong DTBO policy.');
  } else if (manifest.dtbo) throw new Error('Unexpected DTBO policy.');
  for (const name of required) {
    if (!/^[a-f0-9]{64}$/.test(manifest.sha256[name] || '')) throw new Error('Missing checksummed image: ' + name);
  }
  return required;
}

export async function inspectSparse(blob) {
  if (blob.size < 28 || blob.size > maxRootfs) throw new Error('Unsupported rootfs size; use the native installer.');
  const header = new DataView(await blob.slice(0, 28).arrayBuffer());
  if (header.getUint32(0, true) !== 0xed26ff3a || header.getUint16(4, true) !== 1 ||
      header.getUint16(8, true) !== 28 || header.getUint16(10, true) !== 12 ||
      header.getUint32(12, true) !== 4096) throw new Error('Browser installation requires a standard Android sparse image.');
  const blocks = header.getUint32(16, true), chunks = header.getUint32(20, true);
  if (!blocks || blocks * 4096 > maxRootfs || !chunks || chunks > 100000) throw new Error('Invalid sparse image dimensions.');
  let offset = 28, written = 0, largest = 0;
  for (let index = 0; index < chunks; index++) {
    if (offset + 12 > blob.size) throw new Error('Truncated sparse image.');
    const chunk = new DataView(await blob.slice(offset, offset + 12).arrayBuffer());
    const type = chunk.getUint16(0, true), count = chunk.getUint32(4, true), size = chunk.getUint32(8, true);
    const payload = size - 12;
    if (payload < 0 || offset + size > blob.size ||
        ![0xcac1, 0xcac2, 0xcac3, 0xcac4].includes(type) ||
        (type === 0xcac1 && payload !== count * 4096) ||
        ([0xcac2, 0xcac4].includes(type) && payload !== 4) ||
        (type === 0xcac3 && payload !== 0) || (type === 0xcac4 && count !== 0)) throw new Error('Invalid sparse chunk.');
    largest = Math.max(largest, payload);
    written += count;
    offset += size;
  }
  if (offset !== blob.size || written !== blocks) throw new Error('Sparse image dimensions do not match its contents.');
  return { largest, bytes: blocks * 4096 };
}

export async function prepareBundle(file, release, storage, tempName, status = () => {}) {
  if (file.size !== release.size || file.size > 2 * 1024 * MiB || !/^[a-f0-9]{64}$/.test(release.sha256)) throw new Error('Choose the current native ZIP shown on this page.');
  if (!/^porthole-[a-f0-9-]+\.img$/.test(tempName)) throw new Error('Invalid temporary filename.');
  status('Verifying the complete bundle…', 0);
  if (await digestBlob(file, value => status('Verifying the complete bundle…', value)) !== release.sha256) throw new Error('Bundle checksum mismatch. No phone was contacted.');
  const archive = new ZipReader(new BlobReader(file), { useWebWorkers: false });
  const dispose = () => storage.removeEntry(tempName).catch(() => {});
  try {
    const entries = await archive.getEntries();
    if (entries.length > 512 || new Set(entries.map(e => e.filename)).size !== entries.length) throw new Error('Invalid bundle entries.');
    const entry = name => {
      const found = entries.find(item => item.filename === name && !item.directory);
      if (!found) throw new Error('Missing file: ' + name);
      return found;
    };
    if (entry('bundle.json').uncompressedSize > 65536) throw new Error('Oversized bundle manifest.');
    const manifest = JSON.parse(await entry('bundle.json').getData(new TextWriter()));
    validateManifest(manifest, release);
    const smallImage = async (name, limit) => {
      if (entry(name).uncompressedSize > limit) throw new Error('Image exceeds browser limits: ' + name);
      const blob = await entry(name).getData(new BlobWriter());
      if (await digestBlob(blob) !== manifest.sha256[name]) throw new Error('Image checksum mismatch: ' + name);
      return blob;
    };
    const boot = await smallImage('boot.img', 64 * MiB);
    const dtbo = manifest.dtbo ? await smallImage('dtbo.img', MiB) : null;
    const rootEntry = entry(manifest.device + '.img.gz');
    const handle = await storage.getFileHandle(tempName, { create: true });
    const target = await handle.createWritable();
    let size = 0;
    const compressedHash = await createSHA256(); compressedHash.init();
    const gunzip = new DecompressionStream('gzip');
    const completed = gunzip.readable.pipeThrough(new TransformStream({
      transform(chunk, controller) {
        size += chunk.byteLength;
        if (size > maxRootfs) throw new Error('Rootfs exceeds browser storage limits; use the native installer.');
        controller.enqueue(chunk);
      },
    })).pipeTo(target);
    // Attach rejection handling before feeding ZIP data into the decompressor.
    completed.catch(() => {});
    const hashStream = new TransformStream({ transform(chunk, controller) { compressedHash.update(chunk); controller.enqueue(chunk); } });
    const compressed = hashStream.readable.pipeTo(gunzip.writable); compressed.catch(() => {});
    status('Preparing the rootfs in temporary browser storage…', 0);
    try {
      await rootEntry.getData(hashStream.writable, { onprogress: (read, total) => status('Preparing the rootfs…', read / total) });
      await compressed;
      await completed;
    } catch (error) {
      await hashStream.writable.abort(error).catch(() => {});
      await target.abort(error).catch(() => {});
      throw error;
    }
    if (compressedHash.digest() !== manifest.sha256[manifest.device + '.img.gz']) throw new Error('Rootfs checksum mismatch.');
    const rootfs = await handle.getFile();
    const sparse = await inspectSparse(rootfs);
    status('Images verified and ready. No phone has been contacted.', 1);
    return { manifest, rootfs, boot, dtbo, sparse, dispose };
  } catch (error) { await dispose(); throw error; }
  finally { await archive.close(); }
}

export async function preflight(driver, prepared) {
  if (await driver.getVariable('product') !== prepared.manifest.product) throw new Error('Wrong phone model. No writes performed.');
  if (await driver.getVariable('unlocked') !== 'yes') throw new Error('Bootloader is locked. Use the device guide to unlock it first.');
  const value = await driver.getVariable('max-download-size');
  if (!value || !/^(?:0x)?[a-f0-9]+$/i.test(value)) throw new Error('Bootloader download limit unavailable; use native installation.');
  const limit = Math.min(parseInt(value, 16), 128 * MiB);
  if (prepared.sparse.largest + 2 * MiB > limit) throw new Error('Sparse chunks exceed this bootloader’s browser transfer limit. Use native installation.');
  const partition = await driver.getVariable('partition-size:userdata');
  if (partition && /^(?:0x)?[a-f0-9]+$/i.test(partition) && parseInt(partition, 16) < prepared.sparse.bytes) throw new Error('Rootfs is too large for this phone.');
  driver._getDownloadSize = async () => limit - MiB;
  return limit;
}

export async function installImages(driver, prepared, confirmation, progress = () => {}) {
  if (confirmation !== prepared.manifest.device) throw new Error('Type the device codename to confirm erasing all data.');
  await preflight(driver, prepared);
  for (const [name, image] of [['userdata', prepared.rootfs], ['boot_' + prepared.manifest.slot, prepared.boot],
                              ...(prepared.dtbo ? [['dtbo_' + prepared.manifest.slot, prepared.dtbo]] : [])]) {
    progress('Flashing ' + name + '…', 0);
    await driver.flashBlob(name, image, value => progress('Flashing ' + name + '…', value));
  }
  await driver.runCommand('set_active:' + prepared.manifest.slot);
  await driver.runCommand('reboot');
}

export async function bindDevice(selected, manifest) {
  const driver = new FastbootDevice();
  const usbIdentity = selected.serialNumber;
  if (!usbIdentity) throw new Error('The phone did not report a USB serial number. Use native installation.');
  driver.device = selected;
  // The pinned library's public connect() auto-binds newly connected devices.
  // Connect once through its validator instead; never install reconnect listeners.
  try { await driver._validateAndConnectDevice(); }
  catch (error) { await selected.close().catch(() => {}); throw error; }
  let disconnected = false;
  const onDisconnect = event => { if (event.device === selected) disconnected = true; };
  navigator.usb.addEventListener('disconnect', onDisconnect);
  const guard = () => {
    if (disconnected || driver.device !== selected || !selected.opened || selected.serialNumber !== usbIdentity) throw new Error('Phone disconnected or changed. Installation stopped; no automatic reboot.');
  };
  const command = driver.runCommand.bind(driver), upload = driver.upload.bind(driver);
  const writes = new Set(['flash:userdata', 'flash:boot_' + manifest.slot, 'set_active:' + manifest.slot, 'reboot']);
  if (manifest.dtbo) writes.add('flash:dtbo_' + manifest.slot);
  driver.runCommand = async (value) => {
    guard();
    if (!value.startsWith('getvar:') && !/^download:[a-f0-9]{8}$/i.test(value) && !writes.has(value)) throw new Error('Unexpected fastboot operation blocked: ' + value);
    return command(value);
  };
  driver.upload = async (name, data, progress) => {
    guard();
    if (data.byteLength > 128 * MiB) throw new Error('Transfer exceeds browser safety limit.');
    return upload(name, data, progress);
  };
  return { driver, usbIdentity, close: async () => { navigator.usb.removeEventListener('disconnect', onDisconnect); await selected.close(); } };
}
