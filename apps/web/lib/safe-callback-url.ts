/**
 * Post-login landing paths arrive in the query string (`?callbackUrl=`), so they
 * are attacker-controlled. The login page hands the value to `redirect()` for an
 * already-signed-in user, which used to send them to any URL a crafted
 * /login?callbackUrl=https://evil.example link named. Only same-origin paths
 * survive; everything else falls back.
 *
 * Beyond "starts with a single /", these are refused on purpose:
 *  - `\` anywhere – browsers read it as `/`, so `/\evil.example` is the
 *    scheme-relative `//evil.example`.
 *  - control characters – the URL parser drops tab / CR / LF before parsing, so
 *    `/<TAB>/evil.example` also becomes `//evil.example`.
 *  - anything the URL parser would still resolve off-origin (the backstop for
 *    cases not listed here).
 * Over-long values are refused too: the result is echoed into `Location`
 * headers, which the proxy chain in front of the app limits.
 */
const DEFAULT_CALLBACK_URL = "/processes";
const MAX_LENGTH = 2048;
const PROBE_ORIGIN = "http://callback.invalid";

export function safeCallbackUrl(
  raw: string | null | undefined,
  fallback: string = DEFAULT_CALLBACK_URL,
): string {
  if (!raw || raw.length > MAX_LENGTH) return fallback;
  if (raw[0] !== "/" || raw[1] === "/") return fallback;
  // eslint-disable-next-line no-control-regex -- control characters are the point
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return fallback;
  try {
    if (new URL(raw, PROBE_ORIGIN).origin !== PROBE_ORIGIN) return fallback;
  } catch {
    return fallback;
  }
  return raw;
}
