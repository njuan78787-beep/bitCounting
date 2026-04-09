import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'

// Boot demo mock before any network call if demo mode is active.
// import.meta.env.VITE_DEMO_MODE is embedded by Vite from shell env vars
// (set via vercel.json "env" or local .env.local) — more reliable than __define__.
if (import.meta.env.VITE_DEMO_MODE === 'true') {
  const { installMock } = await import('./api/mock.ts')
  installMock()
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry:                1,
      staleTime:            30_000,
      refetchOnWindowFocus: true,
    },
  },
})

const rootEl = document.getElementById('root')
if (!rootEl) throw new Error('No #root element found')

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
