/**
 * express-adapter.js — serves the two stylesheets for a server-rendered host.
 *
 *   import { themeRouter } from './tzone-theme/express-adapter.js';
 *   app.use(themeRouter);
 *
 * Then point page.shell() at them:
 *   shell({ title, tokensHref: '/tz/tokens.css', themeHref: '/tz/theme.css', ... })
 *
 * No demo content lives here — this module serves files and nothing else.
 */

import express from 'express';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const read = (name) => readFileSync(join(here, name), 'utf8');

export const themeRouter = express.Router();

const CACHE = { 'Cache-Control': 'public, max-age=31536000, immutable' };

themeRouter.get('/tz/tokens.css', (request, response) => {
  response.type('text/css').set(CACHE).send(read('tokens.css'));
});

themeRouter.get('/tz/theme.css', (request, response) => {
  response.type('text/css').set(CACHE).send(read('theme.css'));
});
