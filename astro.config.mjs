// @ts-check

import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://madewithwagtail.org',
  integrations: [react(), sitemap()],
  vite: {
    css: {
      lightningcss: {
        errorRecovery: true,
      },
    },
  },
});
