import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// For GitHub Pages *project* sites the app is served from /<repo>/, so set
// VITE_BASE=/mishka-hub/ (or your repo name) at build time. Defaults to '/'.
export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})
