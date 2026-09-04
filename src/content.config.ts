import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const developers = defineCollection({
  // One directory per developer; the profile page is <dev>/index.md, and
  // sites they built live in <dev>/<site>/index.md (see the sites collection).
  loader: glob({ base: './src/content/developers', pattern: '*/index.md' }),
  schema: z.object({
    // The developer slug is the developer directory name, i.e. the first
    // segment of site entry IDs ('<company>/<site>').
    title: z.string(),
    first_published_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    latest_revision_created_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    // The logo lives at public/images/<id>.max-120x120.webp, derived from
    // the entry ID in code — no frontmatter needed.
    location: z.string().nullable().default(null),
    lat: z.string().nullable().default(null),
    lon: z.string().nullable().default(null),
    company_url: z.string().nullable().default(null),
    // Profile page derives the Twitter profile URL from the handler
    // (https://twitter.com/<twitter_handler>).
    twitter_handler: z.string().nullable().default(null),
    // Profile page derives the GitHub profile URL from the username
    // (https://github.com/<github_user>).
    github_user: z.string().nullable().default(null),
    // Verbatim URLs for profiles on other platforms (Instagram, GitLab,
    // personal sites). Unused by pages for now.
    online_profiles: z.array(z.string()).default([]),
  }),
});

const sites = defineCollection({
  // Site entries live inside their developer directory:
  // <dev>/<site>/index.md. IDs are '<dev>/<site>', which keeps site slugs
  // unique across developers.
  loader: glob({ base: './src/content/developers', pattern: '*/*/index.md' }),
  schema: z.object({
    // The site slug is the last segment of the entry ID
    // ('<company>/<site>' -> 'site'). Multiple companies may share the same
    // site slug ('fertighausde' exists under both christian-peters and
    // fertighaus) — uniqueness comes from the '<company>/' prefix.
    // The company slug is the first segment of the entry ID
    // ('<company>/<site>' -> 'company'), matching the parent folder.
    // All site entries are live; only developer profiles use live: false.
    title: z.string(),
    first_published_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    latest_revision_created_at: z
      .union([z.string(), z.date(), z.null()])
      .transform((v) => (v instanceof Date ? v.toISOString() : (v ?? ''))),
    site_url: z.string().nullable().default(null),
    // The screenshot lives at public/images/<company>/<site>.fill-1200x996.webp,
    // derived from the entry ID in code — no frontmatter needed.
    in_cooperation_with_slug: z.string().nullable().default(null),
    tags: z.array(z.string()).default([]),
  }),
});

export const collections = { developers, sites };
