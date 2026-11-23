import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api } from "../lib/api";
import type { AuthStartResponse, UpdateProfilePayload, UserSummary, MyClassroomResponse } from "../lib/types";

type AuthContextValue = {
  user: UserSummary | null;
  isLoading: boolean;
  classroom: MyClassroomResponse | null;
  classroomLoading: boolean;
  login: (payload: { email: string; password: string }) => Promise<AuthStartResponse>;
  register: (payload: { email: string; username: string; password: string }) => Promise<AuthStartResponse>;
  verifyEmail: (payload: { challenge_id: string; code: string }) => Promise<UserSummary>;
  updateProfile: (payload: UpdateProfilePayload) => Promise<UserSummary>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [classroom, setClassroom] = useState<MyClassroomResponse | null>(null);
  const [classroomLoading, setClassroomLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setClassroom(null);
    if (!user) {
      setClassroomLoading(false);
      return;
    }
    setClassroomLoading(true);
    api.getMyClassroom().then((info) => { if (!cancelled) setClassroom(info); })
      .catch(() => { if (!cancelled) setClassroom(null); })
      .finally(() => { if (!cancelled) setClassroomLoading(false); });
    return () => { cancelled = true; };
  }, [user?.id]);

  const refresh = useCallback(async () => {
    try {
      const response = await api.getCurrentUser();
      setUser(response.user);
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (payload: { email: string; password: string }) => {
    const response = await api.login(payload);
    setUser("user" in response ? response.user : null);
    return response;
  }, []);

  const register = useCallback(async (payload: { email: string; username: string; password: string }) => {
    const response = await api.register(payload);
    setUser("user" in response ? response.user : null);
    return response;
  }, []);

  const verifyEmail = useCallback(async (payload: { challenge_id: string; code: string }) => {
    const response = await api.verifyEmail(payload);
    setUser(response.user);
    return response.user;
  }, []);

  const updateProfile = useCallback(async (payload: UpdateProfilePayload) => {
    const response = await api.updateProfile(payload);
    setUser(response.user);
    return response.user;
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, isLoading, classroom, classroomLoading, login, register, verifyEmail, updateProfile, logout, refresh }),
    [user, isLoading, classroom, classroomLoading, login, register, verifyEmail, updateProfile, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
