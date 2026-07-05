import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// For GitHub Pages *project* sites the app is served from /<repo>/, so
// .github/workflows/deploy-web.yml sets VITE_BASE=/MishkaHub/ at build time
// (matches this repo's real name/case). Defaults to '/' for local dev.
export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})
