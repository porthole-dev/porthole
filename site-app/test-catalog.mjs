import { matches } from './src/catalog.js';
import assert from 'node:assert/strict';
assert(matches('Mesa graphics 26.1', 'mesa graphics', 'main/aarch64', ''));
assert(!matches('Mesa graphics 26.1', 'camera', 'main/aarch64', ''));
assert(!matches('Mesa graphics 26.1', '', 'main/aarch64', 'systemd/main/aarch64'));
assert(matches('Mesa graphics 26.1', ' MESA 26.1 ', 'main/aarch64', 'main/aarch64'));
console.log('catalog matching controls passed');
