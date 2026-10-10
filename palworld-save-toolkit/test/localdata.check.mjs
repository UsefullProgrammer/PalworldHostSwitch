// LocalData.sav parse + round-trip. This file is the client-side half of a
// local world; it needs no custom property decoders, only the FColor struct
// used by Local_MapObjectPaintPalette.
//   node test/localdata.check.mjs <world-dir> [<world-dir> ...]
import { readFileSync } from 'fs';
import { decompress as ooz } from '../docs/vendor/ooz-wasm/index.js';
import { decompressSav } from '../docs/js/sav.js';
import { GvasFile, PALWORLD_TYPE_HINTS } from '../docs/js/gvas.js';

let failed = 0;
for (const dir of process.argv.slice(2)) {
  const { gvas } = await decompressSav(new Uint8Array(readFileSync(dir + '/LocalData.sav')), ooz);
  const g = GvasFile.read(gvas, PALWORLD_TYPE_HINTS, {});
  const out = g.write({});
  const same = out.byteLength === gvas.byteLength && out.every((b, i) => b === gvas[i]);
  if (!same) failed++;
  const sd = g.properties.SaveData.value;
  console.log(`round-trip LocalData.sav: ${same ? 'IDENTICAL' : 'DIFFERS'} (${gvas.byteLength} bytes, ${Object.keys(sd).length} fields) ${dir}`);
  if (g.warnings?.length) console.log('  warnings:', g.warnings);
}
process.exit(failed ? 1 : 0);
