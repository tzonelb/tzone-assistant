export const DEFAULT_NOTIFICATION_PREFERENCES = {
  enabled: true,
  inAppPopup: true,
  desktop: false,
  sound: true,
  suppressActiveConversation: true,
  groupRepeated: true,
  showPreview: true,
  channels: {
    messenger: true,
    whatsapp: true,
    instagram: true,
    telegram: true,
  },
  types: {
    customerMessage: true,
    aiReply: false,
    employeeReply: false,
    assignment: true,
    transfer: true,
    system: true,
  },
};

export function notificationPreferenceKey(user) {
  const identity = user?.id || user?.email || "anonymous";
  return `tzone_notification_settings:${identity}`;
}

export function readNotificationPreferences(user) {
  try {
    const raw = JSON.parse(localStorage.getItem(notificationPreferenceKey(user)) || "{}");
    return {
      ...DEFAULT_NOTIFICATION_PREFERENCES,
      ...raw,
      channels: { ...DEFAULT_NOTIFICATION_PREFERENCES.channels, ...(raw.channels || {}) },
      types: { ...DEFAULT_NOTIFICATION_PREFERENCES.types, ...(raw.types || {}) },
    };
  } catch {
    return DEFAULT_NOTIFICATION_PREFERENCES;
  }
}

export function saveNotificationPreferences(user, preferences) {
  localStorage.setItem(notificationPreferenceKey(user), JSON.stringify(preferences));
  window.dispatchEvent(new CustomEvent("tzone:notification-settings-changed", { detail: preferences }));
}
