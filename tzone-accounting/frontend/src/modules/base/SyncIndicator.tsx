/** The honest status line: what is queued, what was refused, when we last reached the server. */

import { useEffect, useState } from "react";
import { useI18n } from "../../core/i18n";
import { syncEngine, type SyncSnapshot } from "../../core/sync";

export function SyncIndicator() {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState<SyncSnapshot | null>(null);

  useEffect(() => syncEngine.subscribe(setSnapshot), []);

  if (!snapshot) return null;

  const tone =
    snapshot.failed > 0
      ? "danger"
      : snapshot.status === "offline"
        ? "warn"
        : snapshot.status === "error"
          ? "danger"
          : "ok";

  const label =
    snapshot.failed > 0
      ? t("sync.rejected", { count: snapshot.failed })
      : snapshot.status === "offline"
        ? t("sync.offline")
        : snapshot.status === "syncing"
          ? t("sync.syncing")
          : snapshot.pending > 0
            ? t("sync.pending", { count: snapshot.pending })
            : t("sync.upToDate");

  return (
    <button
      type="button"
      className={`sync-pill sync-${tone}`}
      onClick={() => void syncEngine.syncOnce()}
      title={snapshot.lastError ?? snapshot.lastSyncAt ?? ""}
    >
      <span className="sync-dot" />
      {label}
    </button>
  );
}
