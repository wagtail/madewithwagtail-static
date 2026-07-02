import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const developers = defineCollection({
  loader: glob({ base: './src/content/developers', pattern: '**/*.md' }),
  schema: z.object({
    id: z.number(),
    slug: z.string(),
    title: z.string(),
    url: z.string(),
    live: z.boolean().default(true),
    first_published_at: z.union([z.string(), z.date(), z.null()]).transform((v) => (v instanceof Date ? v.toISOString() : v ?? '')),
    latest_revision_created_at: z.union([z.string(), z.date(), z.null()]).transform((v) => (v instanceof Date ? v.toISOString() : v ?? '')),
    company_page_html_missing: z.boolean().default(false),
    logo_url: z.string().nullable().default(null),
    logo_title: z.string().nullable().default(null),
    location: z.string().nullable().default(null),
    location_label: z.string().nullable().default(null),
    lat: z.string().nullable().default(null),
    lon: z.string().nullable().default(null),
    company_url: z.string().nullable().default(null),
    twitter_url: z.string().nullable().default(null),
    twitter_handler: z.string().nullable().default(null),
    github_url: z.string().nullable().default(null),
    github_user: z.string().nullable().default(null),
    site_slugs: z.array(z.string()).default([]),
    api_only_site_slugs: z.array(z.string()).default([]),
  }),
});

const sites = defineCollection({
  loader: glob({ base: './src/content/sites', pattern: '**/*.md' }),
  schema: z.object({
    id: z.number(),
    slug: z.string(),
    title: z.string(),
    url: z.string(),
    live: z.boolean().default(true),
    first_published_at: z.union([z.string(), z.date(), z.null()]).transform((v) => (v instanceof Date ? v.toISOString() : v ?? '')),
    latest_revision_created_at: z.union([z.string(), z.date(), z.null()]).transform((v) => (v instanceof Date ? v.toISOString() : v ?? '')),
    company_slug: z.string(),
    site_url: z.string().nullable().default(null),
    site_screenshot_url: z.string().nullable().default(null),
    site_screenshot_title: z.string().nullable().default(null),
    in_cooperation_with_slug: z.string().nullable().default(null),
    tags: z.array(z.string()).default([]),
  }),
});

export const collections = { developers, sites };
