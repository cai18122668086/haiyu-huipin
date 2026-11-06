export function newcomerCountdown(endsAt: string | null, serverNow: string, elapsedMs: number) {
  const remaining = Date.parse(endsAt ?? "") - Date.parse(serverNow) - Math.max(0, elapsedMs);
  const seconds = Number.isFinite(remaining) ? Math.max(0, Math.ceil(remaining / 1000)) : 0;
  return {seconds, parts:[Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(value=>String(value).padStart(2,"0"))};
}
