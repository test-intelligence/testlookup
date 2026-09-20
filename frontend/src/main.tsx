import './polyfills' // Must run before any other import that may call crypto.randomUUID
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { SWRConfig } from 'swr'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { installGlobalErrorHandlers } from './utils/errorReporting'
import './index.css'
import './store/themeStore' // Apply saved theme on load (before first paint)
import { useAuthStore } from './store/authStore'
import { APP_SWR_CONFIG } from './config/swrConfig'
import { SwrMutateBridge } from './components/SwrMutateBridge'

function SessionCacheBoundary() {
  const generation = useAuthStore((state) => state.sessionGeneration)
  return (
    <SWRConfig key={generation} value={{ ...APP_SWR_CONFIG, provider: () => new Map() }}>
      <SwrMutateBridge />
      <App />
    </SWRConfig>
  )
}

// Install window.onerror + unhandledrejection listeners before anything renders
installGlobalErrorHandlers()

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('Failed to find the root element');
ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <SessionCacheBoundary />
        <Toaster
          position="top-right"
          toastOptions={{
            duration: 4000,
            style: { background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155' },
          }}
        />
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
)
