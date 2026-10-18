// Money is always integer kobo. These helpers never go through floating point.

/** "200,000" or "1500.5" -> kobo (integer), or null if it isn't a valid naira amount. */
export function nairaToKobo(text) {
  const clean = String(text).replace(/[₦,\s]/g, '');
  const match = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(clean);
  if (!match) return null;
  const kobo = (match[2] ?? '').padEnd(2, '0');
  return Number(match[1]) * 100 + Number(kobo);
}

/** 20000000 -> "₦200,000" ; 150050 -> "₦1,500.50" */
export function formatNaira(kobo) {
  const negative = kobo < 0;
  const abs = Math.abs(kobo);
  const naira = Math.floor(abs / 100).toLocaleString('en-NG');
  const rest = abs % 100;
  return `${negative ? '-' : ''}₦${naira}${rest ? `.${String(rest).padStart(2, '0')}` : ''}`;
}

/** Kobo -> plain naira text for an input box, e.g. 20000000 -> "200000". */
export function koboToNairaInput(kobo) {
  const rest = kobo % 100;
  return `${Math.floor(kobo / 100)}${rest ? `.${String(rest).padStart(2, '0')}` : ''}`;
}
