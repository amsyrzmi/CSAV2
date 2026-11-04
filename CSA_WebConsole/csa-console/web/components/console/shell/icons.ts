import {
  Activity,
  BookOpen,
  Briefcase,
  Building2,
  CalendarDays,
  CreditCard,
  Inbox,
  KeyRound,
  Layers,
  LayoutDashboard,
  Mail,
  Users,
  UserRoundCog,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { NAV_ICONS } from "@/lib/shell-state";
import type { NavKey } from "@/lib/navigation";

const BY_NAME: Record<string, LucideIcon> = {
  Activity,
  BookOpen,
  Briefcase,
  Building2,
  CalendarDays,
  CreditCard,
  Inbox,
  KeyRound,
  Layers,
  LayoutDashboard,
  Mail,
  Users,
  UserRoundCog,
  UsersRound,
};

export function navIcon(key: NavKey): LucideIcon {
  return BY_NAME[NAV_ICONS[key]];
}
