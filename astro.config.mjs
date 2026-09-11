// @ts-check

import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import { defineConfig, passthroughImageService } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://madewithwagtail.org',
  integrations: [react(), sitemap()],
  // Serve content images as-is: no resizing, re-encoding, or format conversion.
  image: { service: passthroughImageService() },
  vite: {
    css: {
      lightningcss: {
        errorRecovery: true,
      },
    },
  },
});
