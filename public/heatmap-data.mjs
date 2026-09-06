export function buildHeatmap(hours, days = 14, now = new Date()) {
  const dateKey = (date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const today = dateKey(now);
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  start.setDate(start.getDate() - days + 1);
  const byHour = new Map();
  for (const row of hours) {
    if (!Number.isFinite(row.at) || !Number.isFinite(row.tokens) || row.tokens < 0)
      continue;
    const date = new Date(row.at * 1000);
    const key = `${dateKey(date)}:${date.getHours()}`;
    const previous = byHour.get(key) || { tokens: 0, records: 0 };
    byHour.set(key, {
      tokens: previous.tokens + row.tokens,
      records: previous.records + (row.records || 0),
    });
  }
  let sum = 0,
    records = 0,
    peak = 0,
    activeHours = 0;
  const rows = Array.from({ length: days }, (_, day) => {
    const date = new Date(start);
    date.setDate(start.getDate() + day);
    const key = dateKey(date);
    const cells = Array.from({ length: 24 }, (_, hour) => {
      const at = new Date(date.getFullYear(), date.getMonth(), date.getDate(), hour).getTime() / 1000;
      const future = key > today || (key === today && hour > now.getHours());
      const data = future
        ? { tokens: 0, records: 0 }
        : byHour.get(`${key}:${hour}`) || { tokens: 0, records: 0 };
      sum += data.tokens;
      records += data.records;
      peak = Math.max(peak, data.tokens);
      if (data.tokens) activeHours++;
      return {
        at,
        date: key,
        hour,
        future,
        current: key === today && hour === now.getHours(),
        ...data,
      };
    });
    return { date: key, cells };
  });
  return { rows, sum, records, peak, activeHours };
}

export function intensity(tokens, peak) {
  if (tokens <= 0 || peak <= 0) return 0;
  return Math.min(5, Math.max(1, Math.ceil(Math.sqrt(tokens / peak) * 5)));
}
