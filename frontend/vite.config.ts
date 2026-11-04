import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Pinned rather than using Vite's default 5173: another project on this
    // machine uses it, and Vite's auto-increment would land on a different
    // port each run — which breaks the backend's CORS allow-list and the
    // post-OAuth redirect. strictPort makes a clash fail loudly instead.
    port: 5180,
    strictPort: true,
  },
})
