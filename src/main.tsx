import React from 'react';
import ReactDOM from 'react-dom/client';
import { ThemeProvider } from 'next-themes';
import { PortalUiProvider } from '@mine-troutfarm/ui';
import App from './app/App';
import { isPublicMinutesPage } from './app/api';
import './styles/index.css';

const PUBLIC_MINUTES_MODE = isPublicMinutesPage();
const PUBLIC_MINUTES_THEME_STORAGE_KEY = 'mine_city_minutes_theme_v1';
const PUBLIC_MINUTES_PALETTE_STORAGE_KEY = 'mine_city_minutes_palette_v1';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider
      attribute="class"
      defaultTheme="light"
      enableSystem={false}
      disableTransitionOnChange
      storageKey={PUBLIC_MINUTES_MODE ? PUBLIC_MINUTES_THEME_STORAGE_KEY : 'theme'}
    >
      <PortalUiProvider
        storageKey={PUBLIC_MINUTES_MODE ? PUBLIC_MINUTES_PALETTE_STORAGE_KEY : undefined}
        defaultPaletteId={PUBLIC_MINUTES_MODE ? 'beppu-bentenike' : undefined}
      >
        <App />
      </PortalUiProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
