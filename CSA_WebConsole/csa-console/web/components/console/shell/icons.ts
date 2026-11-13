import {
  Activity,
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
  Receipt,
  Users,
  UserRoundCog,
  UsersRound,
  Wallet,
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
  GraduationCap,
  Inbox,
  KeyRound,
  Layers,
  LayoutDashboard,
  Mail,
  Plug,
  Receipt,
  Users,
  UserRoundCog,
  UsersRound,
  Wallet,
};

export function navIcon(key: NavKey): LucideIcon {
  return BY_NAME[NAV_ICONS[key]];
}
