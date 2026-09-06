#!/usr/bin/env node
/* global process */
/**
 * build.js — regenerate tokens.css from tokens.js, and copy both stylesheets
 * into a static directory for server-rendered hosts.
 *
 *   node tzone-theme/build.js            # regenerate tokens.css in place
 *   node tzone-theme/build.js public     # …and copy tokens.css + theme.css there
 */

import { writeFileSync, readFileSync, mkdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { rootVars } from './tokens.js';

const here = dirname(fileURLToPath(import.meta.url));

const banner = [
  '/* T-ZONE design tokens — GENERATED from tokens.js. Do not edit by hand.',
  '   Regenerate with:  node tzone-theme/build.js',
  '   Every colour, font, space, radius and shadow in the product comes from here. */',
  '',
  '',
].join('\n');

const tokensPath = join(here, 'tokens.css');
writeFileSync(tokensPath, banner + rootVars(), 'utf8');
console.log('wrote ' + tokensPath + ' (' + statSync(tokensPath).size.toLocaleString() + ' bytes)');

const target = process.argv[2];
if (target) {
  mkdirSync(target, { recursive: true });
  for (const name of ['tokens.css', 'theme.css']) {
    const out = join(target, name);
    writeFileSync(out, readFileSync(join(here, name)), 'utf8');
    console.log('copied ' + out);
  }
}
