import {
  Activity,
  Banknote,
  BookOpen,
  Briefcase,
  Building2,
  CalendarDays,
  CreditCard,
  GraduationCap,
  Inbox,
  KeyRound,
  Layers,
  LayoutDashboard,
  Mail,
  Plug,
  Users,
  UserRoundCog,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import { NAV_ICONS } from "@/lib/shell-state";
import type { NavKey } from "@/lib/navigation";

const BY_NAME: Record<string, LucideIcon> = {
  Activity,
  Banknote,
  BookOpen,
  Briefcase,
  Building2,
  CalendarDays,
  CreditCard,
  GraduationCap,
  Inbox,
  KeyRound,
  Layers,
  LayoutDashboard,
  Mail,
  Plug,
  Users,
  UserRoundCog,
  UsersRound,
};

export function navIcon(key: NavKey): LucideIcon {
  return BY_NAME[NAV_ICONS[key]];
}
