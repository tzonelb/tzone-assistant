/**
 * page.js — full HTML document shell for server-rendered pages.
 *
 * No copy, no data: every string arrives as a prop.
 */

import { GOOGLE_FONTS_HREF, runtimeVars } from './tokens.js';

/**
 * @param {{title:string, dir?:'ltr'|'rtl', lang?:string,
 *          tokensHref?:string, themeHref?:string, head?:string}} props
 */
export function head(props = {}) {
  return '<!DOCTYPE html>\n' +
    '<html lang="' + (props.lang || 'en') + '" dir="' + (props.dir || 'ltr') + '">\n' +
    '<head>\n' +
    '<meta charset="utf-8">\n' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n' +
    '<title>' + (props.title || '') + '</title>\n' +
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n' +
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n' +
    '<link rel="stylesheet" href="' + GOOGLE_FONTS_HREF + '">\n' +
    '<link rel="stylesheet" href="' + (props.tokensHref || '/static/tokens.css') + '">\n' +
    '<link rel="stylesheet" href="' + (props.themeHref || '/static/theme.css') + '">\n' +
    (props.head || '') +
    '</head>';
}

/**
 * @param {{title:string, rail?:string, topBar?:string, content?:string,
 *          theme?:object, dir?:'ltr'|'rtl', lang?:string,
 *          tokensHref?:string, themeHref?:string, head?:string,
 *          bodyEnd?:string}} props
 */
export function shell(props = {}) {
  return head(props) + '\n' +
    '<body class="tz-root" style="' + runtimeVars(props.theme) + '">\n' +
    '<div style="display:flex;height:100vh;overflow:hidden">\n' +
    (props.rail ? '<aside class="tz-aside">' + props.rail + '</aside>\n' : '') +
    '<div style="flex:1;min-width:0;display:flex;flex-direction:column">\n' +
    (props.topBar || '') +
    '<main style="flex:1;min-height:0;overflow-y:auto">' + (props.content || '') + '</main>\n' +
    '</div>\n</div>\n' +
    (props.bodyEnd || '') +
    '</body>\n</html>';
}
