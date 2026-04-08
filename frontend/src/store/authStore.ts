// =============================================================================
// src/store/authStore.ts
// Zustand store for authentication state.
// =============================================================================

import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { MeResponse, Role } from '../types/api'
import { setAccessToken, clearTokens, setRefreshToken } from '../api/client'

export interface AuthUser {
  user_id:     string
  username:    string
  role:        Role
  client_id:   string | null
  permissions: string[]
}

interface AuthState {
  user:         AuthUser | null
  isLoading:    boolean
  // MFA flow intermediates
  tempToken:    string | null

  // Actions
  setTempToken:  (token: string) => void
  setFullAuth:   (user: MeResponse, accessToken: string, refreshToken: string) => void
  logout:        () => void
  setUser:       (user: MeResponse) => void
  setLoading:    (v: boolean) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user:      null,
      isLoading: false,
      tempToken: null,

      setTempToken: (token) => set({ tempToken: token }),

      setFullAuth: (meResp, accessToken, refreshToken) => {
        setAccessToken(accessToken)
        setRefreshToken(refreshToken)
        set({
          tempToken: null,
          user: {
            user_id:     meResp.user_id,
            username:    meResp.username,
            role:        meResp.role,
            client_id:   meResp.client_id,
            permissions: meResp.permissions,
          },
        })
      },

      setUser: (meResp) =>
        set({
          user: {
            user_id:     meResp.user_id,
            username:    meResp.username,
            role:        meResp.role,
            client_id:   meResp.client_id,
            permissions: meResp.permissions,
          },
        }),

      logout: () => {
        clearTokens()
        set({ user: null, tempToken: null })
      },

      setLoading: (v) => set({ isLoading: v }),
    }),
    {
      name:    'bc-auth',
      storage: createJSONStorage(() => sessionStorage),
      // Only persist user info, not tokens (tokens live in memory / sessionStorage directly)
      partialize: (s) => ({ user: s.user }),
    },
  ),
)

// Convenience selectors
export const useUser    = () => useAuthStore((s) => s.user)
export const useRole    = () => useAuthStore((s) => s.user?.role)
export const useIsAdmin = () => useAuthStore((s) => s.user?.role === 'EXIMIA_ADMIN')
export const useIsCpa   = () =>
  useAuthStore((s) =>
    s.user?.role === 'CPA_PARTNER' ||
    s.user?.role === 'CPA_SENIOR' ||
    s.user?.role === 'EXIMIA_ADMIN',
  )
