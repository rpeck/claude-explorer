import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { queryClient } from '@/lib/queryClient'
import { SettingsProvider, useSettings } from '@/contexts/SettingsContext'
import { SourceFilterProvider } from '@/contexts/SourceFilterContext'
import { SearchPanelProvider } from '@/contexts/SearchPanelContext'
import { SearchPinProvider } from '@/contexts/SearchPinContext'
import { KeyboardNavigationProvider } from '@/contexts/KeyboardNavigationContext'
import { FilterProvider } from '@/contexts/FilterContext'
import { BookmarkProvider } from '@/contexts/BookmarkContext'
import { FetchPipelineProvider } from '@/contexts/FetchPipelineContext'
import { RootLayout } from '@/components/layout/RootLayout'
import { ConversationPage } from '@/routes/ConversationPage'
import { SettingsPage } from '@/routes/SettingsPage'
import { ConnectionStatus } from '@/components/ConnectionStatus'
import { KeyboardHelpModal } from '@/components/KeyboardHelpModal'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { ServerStoppedScreen } from '@/components/ServerStoppedScreen'
import { useServerStopped } from '@/lib/serverStopped'

function ThemeApplier({ children }: { children: React.ReactNode }) {
  const { effectiveTheme } = useSettings()

  useEffect(() => {
    const root = document.documentElement
    if (effectiveTheme === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
  }, [effectiveTheme])

  return <>{children}</>
}

function KeyboardShortcutHandler() {
  useKeyboardShortcuts()
  return null
}

function App() {
  // After the power button stops the server, replace the whole app. The
  // unmount stops every poller and query. UX.md → "Quit (power button)".
  const serverStopped = useServerStopped()
  if (serverStopped) return <ServerStoppedScreen />

  return (
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <ThemeApplier>
          <SourceFilterProvider>
            <FilterProvider>
            <BookmarkProvider>
            <FetchPipelineProvider>
              <BrowserRouter>
                <SearchPinProvider>
                <SearchPanelProvider>
                <KeyboardNavigationProvider>
                  <KeyboardShortcutHandler />
                  <KeyboardHelpModal />
                  <Routes>
                  <Route element={<RootLayout />}>
                    <Route index element={<Navigate to="/conversations" replace />} />
                    <Route path="conversations" element={<ConversationPage />} />
                    <Route path="conversations/:uuid" element={<ConversationPage />} />
                    <Route path="projects/:projectSlug" element={<ConversationPage />} />
                    <Route path="settings" element={<SettingsPage />} />
                  </Route>
                  </Routes>
                </KeyboardNavigationProvider>
                </SearchPanelProvider>
                </SearchPinProvider>
              </BrowserRouter>
              <Toaster position="top-center" richColors closeButton />
              <ConnectionStatus />
            </FetchPipelineProvider>
            </BookmarkProvider>
            </FilterProvider>
          </SourceFilterProvider>
        </ThemeApplier>
      </SettingsProvider>
    </QueryClientProvider>
  )
}

export default App