// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';

// https://astro.build/config
export default defineConfig({
  site: 'https://wagtail.github.io',
  base: '/madewithwagtail-static',
  // site: 'https://madewithwagtail.org',
  integrations: [react(), sitemap()],
  vite: {
    css: {
      lightningcss: {
        errorRecovery: true,
      },
    },
  },
});
