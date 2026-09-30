import { bindDevice, prepareBundle, preflight, installImages, maxRootfs } from './web-install.js';

export async function setupWebInstaller() {
  const mount = document.getElementById('web-installer');
  if (!mount || mount.dataset.ready) return;
  mount.dataset.ready = 'true';
  const element = name => document.getElementById('install-' + name);
  const release = element('release'), file = element('file'), prepare = element('prepare'), connect = element('connect');
  const start = element('start'), discard = element('discard'), confirm = element('confirm');
  const backup = element('backup'), experimental = element('experimental');
  const status = element('status'), progress = element('progress'), download = element('download');
  let releases = [], prepared = null, connection = null, busy = false;
  const message = (text, value) => { status.textContent = text; if (value !== undefined) progress.value = value; };
  const controls = () => {
    release.disabled = file.disabled = busy || !!prepared || !releases.length;
    prepare.disabled = busy || !!prepared || !file.files.length;
    connect.disabled = busy || !prepared || !!connection;
    discard.disabled = busy || !prepared;
    confirm.disabled = backup.disabled = experimental.disabled = busy || !connection;
    start.disabled = busy || !connection || !backup.checked || !experimental.checked || confirm.value.trim() !== prepared?.manifest.device;
  };
  const forget = async () => {
    if (connection) await connection.close().catch(() => {});
    connection = null;
    if (prepared) await prepared.dispose();
    prepared = null;
    confirm.value = ''; backup.checked = experimental.checked = false;
    sessionStorage.removeItem('porthole-install-temp');
  };
  const attempt = async action => {
    busy = true; controls();
    try { await action(); }
    catch (error) {
      message('Stopped: ' + error.message + ' No further writes or automatic reboot will be attempted.');
      await forget();
    } finally { busy = false; controls(); }
  };
  try {
    if (!window.isSecureContext || !navigator.usb || !navigator.storage?.getDirectory || typeof DecompressionStream === 'undefined') throw new Error('Use a current desktop Chrome or Edge browser, or use the native installer.');
    const response = await fetch(new URL('install-releases.json', new URL('../', location.href)));
    if (!response.ok) throw new Error('Release information unavailable; use the downloads page.');
    releases = await response.json();
    if (!releases.length) throw new Error('No verified native bundle is currently available for browser installation.');
    release.replaceChildren(...releases.map((item, index) => new Option(item.name + ' · ' + item.tag, String(index))));
    const update = () => { download.href = releases[Number(release.value)].url; download.hidden = false; };
    release.addEventListener('change', update); update();
    const storage = await (await navigator.storage.getDirectory()).getDirectoryHandle('porthole-installer', { create: true });
    const abandoned = sessionStorage.getItem('porthole-install-temp');
    if (abandoned && /^porthole-[a-f0-9-]+\.img$/.test(abandoned)) await storage.removeEntry(abandoned).catch(() => {});
    sessionStorage.removeItem('porthole-install-temp');
    file.addEventListener('change', controls);
    for (const input of [confirm, backup, experimental]) input.addEventListener('input', controls);
    prepare.addEventListener('click', () => attempt(async () => {
      const quota = await navigator.storage.estimate();
      if (!quota.quota || quota.quota - (quota.usage || 0) < maxRootfs) throw new Error('At least 8 GiB of free browser storage is required. Use native installation or free space.');
      const name = 'porthole-' + crypto.randomUUID() + '.img';
      sessionStorage.setItem('porthole-install-temp', name);
      prepared = await prepareBundle(file.files[0], releases[Number(release.value)], storage, name, message);
    }));
    connect.addEventListener('click', () => attempt(async () => {
      const selected = await navigator.usb.requestDevice({ filters: [{ vendorId: 0x18d1, classCode: 0xff, subclassCode: 0x42, protocolCode: 0x03 }] });
      connection = await bindDevice(selected, prepared.manifest);
      await preflight(connection.driver, prepared);
      message('Checked phone ' + connection.usbIdentity + '. Type ' + prepared.manifest.device + ' and confirm both acknowledgements to install.', 0);
    }));
    start.addEventListener('click', () => attempt(async () => {
      // Read all acknowledgements again at the write boundary, not just in the UI.
      if (!backup.checked || !experimental.checked) throw new Error('Confirm backup and experimental status first.');
      await installImages(connection.driver, prepared, confirm.value.trim(), message);
      message('Installation commands completed. The phone is rebooting; this does not establish hardware qualification.', 1);
      await forget();
    }));
    discard.addEventListener('click', () => attempt(async () => { await forget(); message('Prepared images discarded. No phone writes performed.', 0); }));
    window.addEventListener('beforeunload', event => { if (busy) { event.preventDefault(); event.returnValue = ''; } });
    message('Choose the matching native ZIP, then verify it. No phone has been contacted.');
    controls();
  } catch (error) { releases = []; controls(); message(error.message); }
}
