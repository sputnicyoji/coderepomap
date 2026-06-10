export function formatName(first: string, last: string): string {
  return `${first} ${last}`.trim();
}

export const slugify = (value: string): string =>
  value.toLowerCase().replace(/\s+/g, '-');

export const MAX_USERS = 50;

function internalHelper(value: string): string {
  return value;
}

export async function loadAll(): Promise<string[]> {
  internalHelper('seed');
  return [];
}
