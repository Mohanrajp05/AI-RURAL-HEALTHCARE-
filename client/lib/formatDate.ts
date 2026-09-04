// Every dashboard/activity-log timestamp in this app is generated on the
// backend as a UTC value (see backend/mysql_store.py's _utc_iso() for the
// full reasoning) and sent to the frontend as a properly UTC-marked ISO
// string (e.g. "2026-09-03T20:02:22Z"). This app's users are India-based,
// so every one of those timestamps should always display as India
// Standard Time -- not "whatever timezone the viewer's own browser/OS
// happens to be set to" (the default behavior of Date#toLocaleString()),
// which would show a different wall-clock time for an admin checking the
// dashboard while traveling. formatIST() pins the conversion to
// Asia/Kolkata explicitly via Intl's timeZone option instead.
export const IST_TIME_ZONE = "Asia/Kolkata";

/**
 * Format a backend timestamp (UTC-marked ISO string, e.g.
 * "2026-09-03T20:02:22Z") -- or a JS epoch-ms number, for the handful of
 * spots that track a moment with Date.now() instead -- as an India
 * Standard Time date + time string, e.g. "9/4/2026, 1:32:22 am",
 * regardless of the viewer's own browser/OS timezone. Returns "-" for a
 * missing/invalid value.
 */
export function formatIST(input: string | number | null | undefined): string {
  if (input === null || input === undefined || input === "") return "-";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("en-IN", {
    timeZone: IST_TIME_ZONE,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

/** Same as formatIST(), without the seconds -- for compact UI spots. */
export function formatISTShort(input: string | number | null | undefined): string {
  if (input === null || input === undefined || input === "") return "-";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("en-IN", {
    timeZone: IST_TIME_ZONE,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
