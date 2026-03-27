import React from 'react';
import ReactDOM from 'react-dom/client';
import { ThemeProvider } from 'next-themes';
import { PortalUiProvider } from '@mine-troutfarm/ui';
import App from './app/App';
import './styles/index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
      <PortalUiProvider>
        <App />
      </PortalUiProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
