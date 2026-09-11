# Contributing guidelines

Thank you for your interest in this project! Made with Wagtail is a showcase of sites built with [Wagtail](https://wagtail.org), generated as a fully static site with [Astro](https://astro.build) from Markdown content collections.

## Installation

First, clone the repo:

```sh
git clone git+https://github.com/wagtail/madewithwagtail-static
cd madewithwagtail-static
```

> Requirements: [`node`](https://nodejs.org) (see [.node-version](./.node-version)), `npm`, [`just`](https://github.com/casey/just), and [`prek`](https://prek.j178.dev/).

Then you can install the dependencies and run the site locally:

```sh
just install
just serve
```

The site is served at `http://localhost:4321/madewithwagtail-static/`.

## How the site works

- **Pages and components** live in `src/pages` and `src/components` (Astro components).
- **Content** lives in `src/content`:
  - `src/content/sites` – one Markdown file per showcased site.
  - `src/content/developers` – one Markdown file per Wagtail developer/agency.
- The schema for both collections is defined in `src/content.config.ts`.
- **Stylesheets** are Sass files under `src/styles`, compiled with the [Wagtail style guidelines](https://github.com/wagtail/stylelint-config-wagtail) enforced via Stylelint.
- **SVG icons** are defined as an inline sprite in `src/layouts/BaseLayout.astro`, used via the `<Icon>` component.

Content changes are picked up automatically by the dev server. Data is regenerated at build time — there is no database.

## Site submissions

Sites can be submitted through the [site submission form](https://github.com/wagtail/madewithwagtail-static/issues/new?template=site-submission.yml).
A GitHub Actions workflow (`.github/workflows/submission.yml`) then:

1. **Validates** the submission — checks the URL, generates slugs, and rejects duplicates.
2. **Renders** the site in a sandboxed, credential-free job — detects Wagtail fingerprints,
   scans the page's technologies with Wappalyzer, and takes a screenshot.
   Submissions detected as running incompatible technologies (PHP, ASP.NET, Java,
   Wix, Webflow, Squarespace — i.e. not Wagtail sites) are closed with an explanatory
   comment; complementary technologies (React, Vue, Next.js, Astro, Tailwind, …)
   are recorded on the site page and listed in the PR.
3. **Publishes** a pull request with the new content for maintainer review.

Nothing is published automatically: a maintainer reviews and merges the pull request,
which closes the original issue. The pipeline's logic lives in
`scripts/submissions/process_submission.py` (Python, run with `uv`), with tests under
`tests/submissions/` (`just test-submissions`).

### Optional fields and location data

All fields in the submission form are optional except the submission type, site URL,
title, description, developer name, and the two confirmations. Skipped optional fields
(developer URL, developer location, latitude/longitude, GitHub username, logo URL,
other notes, tags) are recorded
by GitHub as `_No response_` and treated by the pipeline as "not provided".

- **New developer profiles**: skipped location fields are written to the profile's
  frontmatter as empty values (`location: null`, `lat: null`, `lon: null`) — see the
  schema in `src/content.config.ts`. The profile page simply omits the map and location
  line until a maintainer fills them in by editing
  `src/content/developers/<developer>/index.md`.
- **Developer logo (new profiles)**: taken from the Logo URL field if provided,
  otherwise discovered from the developer's own site (the Developer URL) — the
  submitted site's favicon is never used. If neither is available, the profile
  is committed without a logo and a maintainer adds one manually.
- **Already-listed developers**: a submission only adds the new site page — the existing
  profile (including its location) is never modified. If the site's page needs a new
  location or corrected profile details, edit the profile in the same pull request.

## Quality assurance

Here are the available tooling scripts for the project:

```sh
just build   # Build the production site to `dist/`.
just check   # Run the Astro type checker.
just format  # Run all formatters.
just help    # List all the justfile recipes.
just install # Install the dependencies.
just lint    # Run all linters.
just serve   # Run the development server at localhost:4321.
```

Tooling used:

- [Biome](https://biomejs.dev) lints and formats the Astro, TypeScript, and JSON code (`npm run lint:format`).
- [Stylelint](https://stylelint.io) with [@wagtail/stylelint-config-wagtail](https://github.com/wagtail/stylelint-config-wagtail) lints the stylesheets, with [Prettier](https://prettier.io) for their formatting (`npm run lint:css`).
- [`astro check`](https://docs.astro.build/en/reference/cli-reference/#astro-check) type-checks the project (`just check`).
- [`prek`](https://prek.j178.dev/) runs all checks as git pre-commit hooks, and in CI.

## Code review

Create a pull request with your changes so that they can be reviewed by a maintainer. Ensure that you give a summary with the purpose of the change and any steps that the reviewer needs to take to test your work.

All CI checks must pass before a pull request can be merged.

## Deployment

The site is deployed to GitHub Pages on every push to `main`, via GitHub Actions (see [.github/workflows](./.github/workflows)). Dependency updates are automated with [Renovate](https://docs.renovatebot.com/).
