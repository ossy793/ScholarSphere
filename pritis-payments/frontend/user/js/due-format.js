// Shared wording and formatting for dues on the user site.

export const PHASES = {
  draft: { label: 'Draft', badge: 'badge' },
  pending: { label: 'Waiting for approval', badge: 'badge badge-invited' },
  rejected: { label: 'Not approved', badge: 'badge badge-disabled' },
  approved: { label: 'Approved · not live', badge: 'badge' },
  live: { label: 'Live', badge: 'badge badge-active' },
  edit_pending: { label: 'Live · change waiting for approval', badge: 'badge badge-invited' },
};

/** "Sun, 30 Nov 2026, 11:59 pm" in Lagos time. */
export function formatDeadline(iso) {
  return new Date(iso).toLocaleString('en-NG', {
    timeZone: 'Africa/Lagos', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

/** <input type="datetime-local"> value (Lagos time) -> ISO string with the +01:00 offset. */
export function lagosInputToIso(value) {
  return value ? `${value.length === 16 ? `${value}:00` : value}+01:00` : '';
}

/** ISO -> value for <input type="datetime-local">, in Lagos time. */
export function isoToLagosInput(iso) {
  const lagos = new Date(new Date(iso).getTime() + 60 * 60 * 1000); // Lagos is UTC+1 all year
  return lagos.toISOString().slice(0, 16);
}
