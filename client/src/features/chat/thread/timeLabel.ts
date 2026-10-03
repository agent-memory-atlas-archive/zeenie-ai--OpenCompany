/**
 * When a message was written, the way a person says it: the time today,
 * "Yesterday, 9:41 AM", the weekday within a week, else the date. Empty when
 * the timestamp cannot be read.
 */

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

export function timeLabel(timestamp: string | null, now: Date = new Date()): string {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  const time = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  // Rounded: a day with a clock change is 23 or 25 hours long.
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);
  if (days <= 0) return time;
  if (days === 1) return `Yesterday, ${time}`;
  if (days < 7) return `${date.toLocaleDateString(undefined, { weekday: 'long' })}, ${time}`;
  const year = date.getFullYear() === now.getFullYear() ? undefined : 'numeric';
  return `${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year })}, ${time}`;
}
