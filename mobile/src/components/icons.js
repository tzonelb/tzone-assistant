import React from "react";
import Svg, { Path, Circle, Rect } from "react-native-svg";

// Lucide-style stroke icons, paths taken from the approved mobile mockup.
function Icon({ size = 17, color = "currentColor", strokeWidth = 1.5, children }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      {children}
    </Svg>
  );
}

export const InboxIcon = (p) => (
  <Icon {...p}>
    <Path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </Icon>
);

export const CustomersIcon = (p) => (
  <Icon {...p}>
    <Path d="M9 11a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 9 11zM3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16 5.3a3.2 3.2 0 0 1 0 5.4M18 14.8c2 .7 3 2.5 3 5.2" />
  </Icon>
);

export const PublishIcon = (p) => (
  <Icon {...p}>
    <Path d="m3 11 15-6v14L3 13zM7 13v5a2 2 0 0 0 4 0v-3" />
  </Icon>
);

export const MoreIcon = (p) => (
  <Icon {...p}>
    <Circle cx="4" cy="12" r="1" />
    <Circle cx="12" cy="12" r="1" />
    <Circle cx="20" cy="12" r="1" />
  </Icon>
);

export const BellIcon = (p) => (
  <Icon {...p}>
    <Path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
    <Path d="M13.7 21a2 2 0 0 1-3.4 0" />
  </Icon>
);

export const SearchIcon = (p) => (
  <Icon strokeWidth={1.6} {...p}>
    <Circle cx="11" cy="11" r="7" />
    <Path d="m20 20-3.5-3.5" />
  </Icon>
);

export const BackIcon = (p) => (
  <Icon strokeWidth={1.7} {...p}>
    <Path d="M15 5l-7 7 7 7" />
  </Icon>
);

export const PersonIcon = (p) => (
  <Icon {...p}>
    <Circle cx="12" cy="8" r="3.2" />
    <Path d="M5 20c0-3.5 3-5.8 7-5.8s7 2.3 7 5.8" />
  </Icon>
);

export const SparkIcon = (p) => (
  <Icon {...p}>
    <Path d="M12 3l1.8 4.9L19 9.6l-4.4 3 .6 5.2L12 15.6 8.8 17.8l.6-5.2L5 9.6l5.2-1.7z" />
  </Icon>
);

export const SendIcon = (p) => (
  <Icon strokeWidth={1.7} {...p}>
    <Path d="m4 12 16-8-6 16-2.5-6.5z" />
  </Icon>
);

export const MicIcon = (p) => (
  <Icon strokeWidth={1.6} {...p}>
    <Rect x="9" y="3" width="6" height="11" rx="3" />
    <Path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" />
  </Icon>
);

export const DoubleCheckIcon = (p) => (
  <Icon strokeWidth={2} {...p}>
    <Path d="m2 13 4 4 8-9" />
    <Path d="m10 15 2 2 9-10" />
  </Icon>
);
