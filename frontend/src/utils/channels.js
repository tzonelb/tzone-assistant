/**
 * The channels this platform can actually connect.
 *
 * One list, because there were several. Each screen that needed to name the
 * channels held its own copy, and the copies had drifted: two of them ended in
 * `website`, which the platform has never supported and the Channels screen
 * cannot connect. Every company saw a Website tab on its inbox and a Website
 * option in its notification filter, both permanently empty, one of them
 * captioned "Website is not connected yet" — not connected, and not
 * connectable.
 *
 * The server is the authority: `SUPPORTED_CHANNELS` in
 * `backend/services/channel_account_service.py`, sent as `supported_channels`
 * on the responses that need it. This list is what a screen shows before the
 * first response arrives, and what a screen with no such response uses. Keep it
 * in step with the server; `tests/test_channel_catalogue.py` fails if it drifts.
 */
export const SUPPORTED_CHANNELS = [
  "messenger",
  "whatsapp",
  "instagram",
  "telegram",
];
