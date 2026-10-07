function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatMessageTimestamp(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  const time = `${twoDigits(date.getHours())}:${twoDigits(date.getMinutes())}:${twoDigits(date.getSeconds())}`;
  const day = twoDigits(date.getDate());
  const month = twoDigits(date.getMonth() + 1);
  const year = String(date.getFullYear());
  return `${time} · ${day}/${month}/${year}`;
}
