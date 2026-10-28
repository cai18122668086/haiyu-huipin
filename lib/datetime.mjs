export const BEIJING_TIME_ZONE = "Asia/Shanghai";

/** SQLite returns offsetless UTC; explicit API offsets must remain intact. */
/** @param {string | number | Date} value */
export function parseApiDate(value) {
  if (value instanceof Date) return new Date(value.getTime());
  if (typeof value === "number") return new Date(value);
  let normalized = value.trim();
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(normalized)) {
    normalized = normalized.replace(" ", "T");
    if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized)) normalized += "Z";
  }
  return new Date(normalized);
}

/** @param {string | number | Date} value @param {Intl.DateTimeFormatOptions} [options] */
export function formatBeijingDate(value, options = {}) {
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    ...options, timeZone: BEIJING_TIME_ZONE, hourCycle: "h23",
  }).format(date);
}

/** datetime-local inputs represent Beijing wall time, regardless of the host. */
/** @param {string | null | undefined} value */
export function toBeijingDateInput(value) {
  if (!value) return "";
  const date = parseApiDate(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: BEIJING_TIME_ZONE, hourCycle: "h23", year: "numeric",
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).formatToParts(date);
  const part = (type) => parts.find(item => item.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

/** @param {string} value */
export function beijingDateInputToIso(value) {
  return value ? new Date(`${value}+08:00`).toISOString() : null;
}
