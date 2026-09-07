# Open-code-review findings — fix report

Branch `submission-system`, base HEAD before fixes: `2efb401`.
All seven OCR findings applied in one commit. TDD: tests added first, watched fail (17 failed), implemented, watched pass.

## FIX 1 (high) — `_No response_` placeholder treated as unset

**File:** `scripts/submissions/process_submission.py`, `parse_issue_form_body`.

- Added `NO_RESPONSE_PLACEHOLDER = "_No response_"`; any section whose content equals it is parsed as unset (`[]`), which the downstream `field()` helper maps to `""`/`None` and the `Tags` handler maps to `tags: []`.
- Tests: `tests/submissions/test_parse.py::TestNoResponsePlaceholder` (placeholder in Company URL, Location, Latitude, Longitude, GitHub username, Logo URL, Tags → all unset) and `tests/submissions/test_validate.py::TestBuildProposal::test_no_response_optional_fields_accepted` (body with placeholders → proposal accepted with `company_url/location/lat/lon/github_user = None`, `tags == []`).

## FIX 2 (security) — IPv6 route-guard bypass closed

**File:** `scripts/submissions/process_submission.py`.

- The old `route_guard` did `url.split("/")[2].split(":")[0]`, which yields `[` for `http://[::1]:8080/` — private IPv6 literals sailed through.
- Extracted a testable pure helper `is_private_browser_host(url)`: parses the host with `urlsplit(...).hostname` (strips brackets), classifies via the existing `_is_browsable_ip`, and on a non-IP hostname treats only `localhost` as private. `route_guard` now calls it.
- Tests: `tests/submissions/test_render.py::TestIsPrivateBrowserHost` — 9 private/loopback cases (`[::1]`, `[::1]:8080`, `[fe80::1]`, `[::ffff:127.0.0.1]`, `127.0.0.1`, `169.254.169.254`, `10.0.0.1`, `localhost`, `LOCALHOST:8000`) → blocked; 4 public cases → allowed.
- Residual (unchanged, documented in the helper): hostname-based SSRF inside the browser (DNS rebinding) is backstopped by the credential-free container, per the spec's defense-in-depth.

## FIX 3 (security) — mixed public/private DNS records rejected

**File:** `scripts/submissions/process_submission.py`, `check_public_url`.

- `any(_is_browsable_ip(ip) for ip in ips)` → `all(...)`, message now "does not resolve to a public-only address". An attacker with mixed A/AAAA records can no longer slip a private address past validation while httpx might connect to it.
- Test: fake resolver entry `mixed.example → [10.1.2.3, 93.184.216.34]`; `tests/submissions/test_url.py::test_rejects_mixed_public_private_records` asserts rejection. Existing private-host tests still pass.

## FIX 4 (bug) — workflow trigger requires the form's title prefix

**File:** `.github/workflows/submission.yml` (validate job `if:`).

- Now `contains(github.event.issue.body, '### Submission type') && startsWith(github.event.issue.title, '[Site submission]')`. A manually created issue merely quoting the marker no longer enters the pipeline (and can no longer be auto-closed by it).

## FIX 5 (bug) — section boundaries limited to the 13 known form labels

**File:** `scripts/submissions/process_submission.py`, `parse_issue_form_body`.

- Replaced `re.split(r"^### ", ...)` with `SECTION_RE` matching only the 13 exact form headings; user text containing `### Features` now stays inside the field it was typed in.
- Anti-forgery hardening beyond the OCR suggestion: the form renders sections in a fixed canonical order (`FORM_HEADINGS`), so the parser accepts the first in-order occurrence of each field heading and skips out-of-order/duplicate matches (forged headings typed inside free text). Confirmations — always the form's final section — takes the **last** occurrence, so a forged `### Confirmations` inside a description can no longer shadow the real checkboxes (found by the new test; first-wins alone got this wrong).
- Tests: `TestSectionBoundaries::test_unknown_heading_stays_in_previous_field` (description containing `### Features` round-trips intact) and `::test_first_known_heading_wins_over_forged_duplicate` (forged Confirmations inside the description is ignored; real checkboxes win).
- Residual: a forged heading that happens to be the *next expected* field (e.g. `### Developer` typed inside the description) can shadow the real section; the pipeline then either fails closed (rejection) or produces odd-but-human-reviewed PR content. Inherent to an unsigned text protocol; noted, not exploitable for security bypass (URLs are validated wherever they are read from).

## FIX 6 (hardening) — job timeouts

**File:** `.github/workflows/submission.yml`.

- `timeout-minutes`: validate 10, render 15, render-failure-notice 5, publish 20 (was: 6-hour GitHub default; most relevant for the render job loading attacker-controlled sites).

## FIX 7 (hardening) — jq empty-file guard in "Handle rejection"

**File:** `.github/workflows/submission.yml`.

- `jq ... || true` plus an empty-`$reasons` fallback to "- The submission could not be processed (unexpected error)." — an unexpected exit-1 crash in validate now still comments on the issue instead of silently aborting the step.

## Verification

| Command | Result |
|---|---|
| `just test-submissions` | **112 passed in 1.22s** (94 before + 18 new/updated) |
| `npm run lint` (Biome + Stylelint) | clean — "No fixes applied" |
| `uvx zizmor .github/workflows/submission.yml` | "No findings to report. Good job! (6 suppressed)" |
| `uv run --with pyyaml python -c "yaml.safe_load(...)"` | valid YAML |
| `npm run check` (astro check) | completes (36 pre-existing hints, no errors; sandbox network warnings only) |

## Files changed

- `scripts/submissions/process_submission.py`
- `.github/workflows/submission.yml`
- `tests/submissions/test_parse.py`
- `tests/submissions/test_validate.py`
- `tests/submissions/test_url.py`
- `tests/submissions/test_render.py`
