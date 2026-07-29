"use client";

import { ProductionApplication } from "./production/production-app";

/**
 * Dedicated production entrypoint. Docker and CI replace app/page.tsx with
 * this file before building, so the production module graph never imports
 * deterministic prototype credentials or business fixtures.
 */
export default function ProductionPage() {
  return <ProductionApplication />;
}
