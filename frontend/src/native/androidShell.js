// Native-app behaviours, active only inside the Capacitor Android build.
// The web build imports this too but everything is behind isNativePlatform()
// so the browser experience is untouched.
import { Capacitor } from "@capacitor/core";

export function initAndroidShell() {
  if (!Capacitor.isNativePlatform()) return;

  document.documentElement.classList.add("native-app");

  // Behave like an app, not a zoomable web page.
  const viewport = document.querySelector('meta[name="viewport"]');
  if (viewport) {
    viewport.setAttribute(
      "content",
      "width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover",
    );
  }

  // Android hardware/gesture back: navigate back inside the app instead of
  // closing it; only exit from a root screen (dashboard/login).
  import("@capacitor/app").then(({ App }) => {
    App.addListener("backButton", () => {
      const path = window.location.pathname;
      const atRoot =
        path === "/" ||
        path === "/dashboard" ||
        path === "/login";
      if (atRoot) {
        App.minimizeApp();
      } else {
        window.history.back();
      }
    });
  });
}
