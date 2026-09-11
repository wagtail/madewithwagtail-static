[Made with Wagtail](https://madewithwagtail.org) [![CI](https://github.com/wagtail/madewithwagtail-static/actions/workflows/ci.yml/badge.svg)](https://github.com/wagtail/madewithwagtail-static/actions/workflows/ci.yml) [<img src="https://raw.githubusercontent.com/wagtail/wagtail/main/docs/logo.png" width="83" align="right" alt="Wagtail">](https://wagtail.org/)
=================

> A showcase of sites and apps made with [Wagtail](https://wagtail.org/): an easy to use, open source content management system.

*Check out [Awesome Wagtail](https://github.com/wagtail/awesome-wagtail) for more awesome packages and resources from the Wagtail community.*

## About this project

This repository powers [madewithwagtail.org](https://madewithwagtail.org). The site is a fully static build — there is no database or server-side application. [Astro](https://astro.build) generates the whole site at build time from Markdown content collections:

- **1,200+ showcased sites**, each with a screenshot, description, tags, and detected front-end technologies.
- **300+ developer profiles** for the agencies and individuals who built them.
- Browsing by tag, paginated site and developer listings, and a sitemap.

The content lives in `src/content`, with one Markdown file per site (`src/content/developers/<developer>/<site>/index.md`) and one per developer profile (`src/content/developers/<developer>/index.md`). The schema for both collections is defined in [`src/content.config.ts`](src/content.config.ts).

## Quick start

Requirements: [`node`](https://nodejs.org) (see [.node-version](.node-version)), `npm`, [`just`](https://github.com/casey/just), and [`prek`](https://prek.j178.dev/).

```sh
git clone git+https://github.com/wagtail/madewithwagtail-static
cd madewithwagtail-static

# Install the dependencies.
just install

# Start the development server at http://localhost:4321/madewithwagtail-static/.
just serve
```

Other useful commands:

```sh
just build            # Build the production site to `dist/`.
just check            # Run the Astro type checker.
just lint             # Run all linters (Biome, Stylelint, prek).
just format           # Run all formatters.
just test-submissions # Run the submission pipeline tests.
just help             # List all the justfile recipes.
```

## Site submissions

Anyone can submit a site through the [site submission form](https://github.com/wagtail/madewithwagtail-static/issues/new?template=site-submission.yml). A GitHub Actions workflow (`.github/workflows/submission.yml`) then:

1. **Validates** the submission — checks the URL, generates slugs, and rejects duplicates.
2. **Renders** the site in a sandboxed, credential-free job — detects Wagtail fingerprints, scans the page's technologies with Wappalyzer, and takes a screenshot. Non-Wagtail sites are closed with an explanatory comment.
3. **Publishes** a pull request with the new content for maintainer review.

Nothing is published automatically: a maintainer reviews and merges the pull request, which closes the original issue. See [CONTRIBUTING.md](CONTRIBUTING.md#site-submissions) for the full workflow.

### Validating submissions

A site is accepted for inclusion on Made with Wagtail if it is made with Wagtail — there is no judgement of a site's quality. The automated pipeline renders each submitted site headlessly and looks for Wagtail fingerprints, rejecting sites running incompatible technologies (PHP, ASP.NET, Java, Wix, Webflow, Squarespace). Complementary technologies (React, Vue, Astro, Tailwind, …) are recorded on the site page. The detection logic lives in [`scripts/submissions/process_submission.py`](scripts/submissions/process_submission.py), with tests under [`tests/submissions/`](tests/submissions/).

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for installation instructions, how the site works, coding standards, and code review guidelines.

## Deployment

The site is deployed to GitHub Pages on every push to `main`, via GitHub Actions. Dependency updates are automated with [Renovate](https://docs.renovatebot.com/).

## License

[MIT](LICENSE)
