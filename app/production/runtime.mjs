const VALID_APP_MODES = new Set(["demo", "production"]);

/**
 * Keep the current hosted prototype backwards compatible while requiring an
 * explicit production opt-in. Unknown values fail closed during startup so a
 * misspelled environment variable can never silently expose demo fixtures.
 *
 * @param {string | undefined | null} value
 * @returns {"demo" | "production"}
 */
export function resolveAppMode(value) {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return "demo";
  if (VALID_APP_MODES.has(normalized)) return normalized;
  throw new Error(
    `Invalid NEXT_PUBLIC_APP_MODE "${value}". Expected "demo" or "production".`,
  );
}

export const appMode = resolveAppMode(process.env.NEXT_PUBLIC_APP_MODE);

/** @returns {boolean} */
export function isProductionMode() {
  return appMode === "production";
}
