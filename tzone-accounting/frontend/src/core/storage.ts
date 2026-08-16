/** Tiny localStorage wrapper with an in-memory fallback, so kernel code also runs under vitest. */

const memory = new Map<string, string>();

const backing: Pick<Storage, "getItem" | "setItem" | "removeItem"> =
  typeof localStorage === "undefined"
    ? {
        getItem: (key) => memory.get(key) ?? null,
        setItem: (key, value) => void memory.set(key, value),
        removeItem: (key) => void memory.delete(key),
      }
    : localStorage;

export function readSetting(key: string): string | null {
  return backing.getItem(key);
}

export function writeSetting(key: string, value: string): void {
  backing.setItem(key, value);
}

export function clearSetting(key: string): void {
  backing.removeItem(key);
}
