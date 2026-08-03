import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// @testing-library/react only auto-registers its afterEach(cleanup) hook
// when it detects a global `afterEach` (i.e. `test.globals: true` in
// vitest.config.js). Register it explicitly here too so mounted trees,
// timers, and event listeners (e.g. NotificationProvider's poll interval
// and focus/visibilitychange listeners) never leak from one test file into
// the next, even if globals ever gets turned off again.
afterEach(cleanup);
