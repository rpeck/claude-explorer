/**
 * The app's providers, for the N2 tests. Import this module with
 * `await import('./harness')` after `setRegistry(...)`, so that the
 * providers and the component under test share one module graph.
 * (It must not import `../mocks/server`: a re-imported copy is a second
 * MSW server that does not listen. `serveApp` is in fakeRegistry.ts.)
 */
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { SettingsProvider } from '@/contexts/SettingsContext'
import { SourceFilterProvider } from '@/contexts/SourceFilterContext'
import { SearchPanelProvider } from '@/contexts/SearchPanelContext'
import { SearchPinProvider } from '@/contexts/SearchPinContext'
import { KeyboardNavigationProvider } from '@/contexts/KeyboardNavigationContext'
import { FilterProvider } from '@/contexts/FilterContext'
import { BookmarkProvider } from '@/contexts/BookmarkContext'
import { FetchPipelineProvider } from '@/contexts/FetchPipelineContext'

export function AppProviders({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } })
  return (
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <SourceFilterProvider>
          <FilterProvider>
            <BookmarkProvider>
              <FetchPipelineProvider>
                <MemoryRouter>
                  <SearchPinProvider>
                    <SearchPanelProvider>
                      <KeyboardNavigationProvider>{children}</KeyboardNavigationProvider>
                    </SearchPanelProvider>
                  </SearchPinProvider>
                </MemoryRouter>
              </FetchPipelineProvider>
            </BookmarkProvider>
          </FilterProvider>
        </SourceFilterProvider>
      </SettingsProvider>
    </QueryClientProvider>
  )
}
