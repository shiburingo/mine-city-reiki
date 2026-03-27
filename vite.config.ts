import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/mine-city-reiki/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    proxy: {
      '/mine-city-reiki-api': {
        target: 'http://127.0.0.1:8795',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/mine-city-reiki-api/, ''),
      },
      '/mine-trout-cash-api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/mine-trout-cash-api/, ''),
      },
    },
  },
  resolve: {
    alias: {
      '@': '/src',
    },
  },
});
