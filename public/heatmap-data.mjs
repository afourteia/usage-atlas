export function buildHeatmap(hours, days = 14, now = new Date()) {
  const end = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );
  const start = new Date(end.getTime() - (days - 1) * 86400000);
  const byHour = new Map();
  for (const row of hours) {
    if (
      !Number.isFinite(row.at) ||
      !Number.isFinite(row.tokens) ||
      row.tokens < 0
    )
      continue;
    const hour = Math.floor(row.at / 3600) * 3600;
    const previous = byHour.get(hour) || { tokens: 0, records: 0 };
    byHour.set(hour, {
      tokens: previous.tokens + row.tokens,
      records: previous.records + (row.records || 0),
    });
  }
  let sum = 0,
    records = 0,
    peak = 0,
    activeHours = 0;
  const rows = Array.from({ length: days }, (_, day) => {
    const date = new Date(start.getTime() + day * 86400000);
    const dateKey = date.toISOString().slice(0, 10);
    const cells = Array.from({ length: 24 }, (_, hour) => {
      const at = date.getTime() / 1000 + hour * 3600;
      const future = at > now.getTime() / 1000;
      const data = future
        ? { tokens: 0, records: 0 }
        : byHour.get(at) || { tokens: 0, records: 0 };
      sum += data.tokens;
      records += data.records;
      peak = Math.max(peak, data.tokens);
      if (data.tokens) activeHours++;
      return {
        at,
        date: dateKey,
        hour,
        future,
        current: Math.floor(now.getTime() / 3600000) * 3600 === at,
        ...data,
      };
    });
    return { date: dateKey, cells };
  });
  return { rows, sum, records, peak, activeHours };
}

export function intensity(tokens, peak) {
  if (tokens <= 0 || peak <= 0) return 0;
  return Math.min(5, Math.max(1, Math.ceil(Math.sqrt(tokens / peak) * 5)));
}
