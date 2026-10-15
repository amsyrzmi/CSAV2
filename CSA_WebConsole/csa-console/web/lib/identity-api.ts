import { api, post } from "./api";
import type { Me, TokenInfo } from "./identity-contracts";

export const identity = {
  me: () => api<Me>("/console/v1/auth/me"),
  login: (email: string, password: string) =>
    post<Me>("/console/v1/auth/login", { email, password }),
  logout: () => post("/console/v1/auth/logout"),
  changePassword: (current_password: string, new_password: string) =>
    post<Me>("/console/v1/auth/password/change", {
      current_password,
      new_password,
    }),
  requestReset: (email: string) =>
    post("/console/v1/auth/password/reset", { email }),
  inspect: (token: string) =>
    post<TokenInfo>("/console/v1/invites/inspect", { token }),
  accept: (token: string, password: string, display_name?: string) =>
    post<Me>("/console/v1/invites/accept", { token, password, display_name }),
};
