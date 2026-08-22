# Agentic 100 — Agent-Readiness Loop (Bramwell Owned Media)

**Aliases:** agentic-100, is-agentic, agent-readiness loop, Bramwell Agentic 100.

**Goal:** take any owned public site to **100/100** on [is-agentic.com](https://is-agentic.com) and keep it there under continuous publishing, using **class-level fixes** and **build-time guards**.

This playbook is the public, agent-findable copy of the loop. It lives in [AuthorityTech/.github](https://github.com/AuthorityTech/.github) so agents and humans can discover it without Jarvis cron.

## Why this repo

AuthorityTech/.github is the public entity layer for AuthorityTech and Machine Relations. Per-repo skills stay in the site repos. This file is the org-level seam: find the loop here, then open the canonical skill in the site you are changing.

## Non-negotiables

1. **Fix by class of surface, not URL.** Find the choke point (layout, schema helper, page template, content contract). Ship one class fix plus a guard so the next publish cannot regress the same class.
2. **The scanner is the oracle.** Read report details. Do not guess a spec, invent checks, or treat a blog post as the scoring model.
3. **Local verify before push.** Hosted scan confirms production. Never "fix forward" against a stale hosted report.
4. **Never invent NAP / address.** If Organization schema is missing `address`, leave it missing unless a real, founder-confirmed PostalAddress exists. Landmark-strip H1 rules: an `h1` inside `header`, `nav`, `footer`, or `aside` does not count as the page title.
5. **Paralax / Para Labs stay an independent third-party voice.** Do not collapse them into AuthorityTech, Machine Relations, or founder owned-media copy.

## The loop

1. **Extract the rubric** from the current is-agentic report for that domain (check IDs, evidence, recommendations, eligibility). The hosted methodology can change; the report you just pulled is the contract for this pass.
2. **Baseline.** Retrieve structured evidence:

   ```sh
   npx -y is-agentic <domain> --json
   ```

   The official CLI returns a completed stored report and **does not force a rescan** when one already exists. To force a fresh oracle snapshot, use the is-agentic **SSE scan stream** or the repo `.agentic` mailbox (see Porting).
3. **Classify by CLASS.** Group every failed or partial check into a surface class (shared layout, JSON-LD helper, content type, error page, robots/llms, etc.). Do not open one PR per URL.
4. **Class fix + guard.** Change the choke point. Add or extend a build-time / `agentic:verify` guard that fails if that class regresses.
5. **Local `agentic:verify`.** Run the site repo's verify script and keep it green.
6. **Merge / deploy.**
7. **Rescan production** (forced, not cached). Confirm **100/100**.
8. Stay in the loop. Continuous publishing is the threat model; the guard is the retention mechanism.

```
extract rubric → baseline → classify by CLASS → class fix + guard → local agentic:verify → merge/deploy → rescan → 100
```

## Oracle (do not guess the spec)

- Public scanner: https://is-agentic.com
- CLI: `npx -y is-agentic <domain>` (human) or `--json` (unchanged API report)
- Report API: `https://is-agentic.com/api/v1/report?url=https%3A%2F%2F<domain>`
- Methodology: https://is-agentic.com/methodology

Scoring (as published by is-agentic): Essential checks share an 80-point pool; Recommended checks share a 20-point pool; Emerging signals can add a small bonus (capped) and never lower a score. Checks that do not apply are excluded, not failed.

Read **every** finding's `id`, `details` / evidence, `recommendation`, `result`, and `tier`. A 100/100 score can still list Recommended gaps. Those gaps are still oracle output — they are not a license to invent NAP, addresses, or brand facts to chase a cleaner report.

## Landmark-strip H1

Page title means a real `h1` in the document content — typically inside `main` — not chrome.

| Location of `h1` | Counts as page H1? |
| --- | --- |
| `main` / article body | Yes |
| `header` | No |
| `nav` | No |
| `footer` | No |
| `aside` | No |

If the scanner says a page has no H1, strip landmarks and look again before adding a second heading. Do not "fix" a site-name H1 in the header by leaving it there and hoping; move or add the page title where content lives.

## Never invent NAP / address

Name, address, and phone are founder-owned facts. The scanner may recommend `PostalAddress` on Organization JSON-LD. If no real address is on file, **do not fabricate one** to close `org-schema-completeness`. 100/100 is achievable without a fake address; inventing NAP creates a permanent entity lie that is worse than a Recommended partial.

Same rule for phone, street, suite, and geo coordinates.

## Voice: Paralax / Para Labs

Paralax and Para Labs are independent third-party voice. When this loop runs on `paralax.ai` or `paralabs.ai`:

- Do not rewrite them as AuthorityTech properties.
- Do not inject Machine Relations category copy, founder bylines, or agency CTAs.
- Keep entity schema, titles, and H1s in *their* voice.
- Class fixes (guards, landmark H1, error pages, machine-readable files) are portable; positioning copy is not.

## Canonical skill copies

Implementation detail — class maps, verify scripts, mailbox workflows — lives in the site repos, not here.

| Copy | Role |
| --- | --- |
| [AuthorityTech/website](https://github.com/AuthorityTech/website) `.claude/skills/agentic-100/SKILL.md` | Canonical skill for AuthorityTech owned media |
| [Bramwell-Inc/machinerelations.ai](https://github.com/Bramwell-Inc/machinerelations.ai) `.claude/skills/agentic-100/` | Proven 100/100 as of 2026-08-22; preferred porting source |

When the two copies drift, prefer the proven MRI copy for the loop mechanics and regenerate that site's class map. Do not "merge by guess."

## Porting

To stand up the loop on another owned public site:

1. Copy the skill directory: `.claude/skills/agentic-100/`
2. Copy `scripts/agentic-verify.mjs` (and the `agentic:verify` package script)
3. **Regenerate the class map** for that site's templates, schema helpers, and content types. Do not reuse MRI's map blindly.
4. Optionally add `agentic-scan.yml` so the `.agentic` mailbox can force a hosted scan
5. Run local `agentic:verify`, then a forced production rescan

Port the mechanism. Do not port another site's entity facts, NAP, or voice.

## Discoverability seams (as of 2026-08-22)

Agents should be able to find this loop from any of these seams. If a seam is missing, restore it — do not invent a sixth channel.

1. Per-repo `.claude/skills/agentic-100/`
2. **This playbook** in AuthorityTech/.github (`profile/agentic-100-playbook.md`)
3. Brain observe facts — query `agentic-100` / `is-agentic`
4. Grok Bot workflow `agentic-100-readiness-loop`
5. Jarvis skill-supply-chain promotion + cron: **DEFERRED** until the founder asks

Do not add Jarvis cron, skill-supply-chain promotion, or a new mailbox protocol unless the founder explicitly asks.

## Portfolio status (as of 2026-08-22)

| Site | Status |
| --- | --- |
| [machinerelations.ai](https://is-agentic.com/scan/machinerelations.ai) | **100/100** (proven) |
| jaxonparrott.com | Next |
| paralax.ai | Next — independent third-party voice |
| paralabs.ai | Next — independent third-party voice |

MRI remaining Recommended findings on the 2026-08-22 snapshot (`brand-search-accuracy`, Organization schema missing `address`) are oracle details, not a prompt to invent NAP or force a generic brand name into search. Keep 100 without fabricating facts.

## Related public surfaces

- Category site: https://machinerelations.ai
- Agency: https://authoritytech.io
- Founder: https://jaxonparrott.com
- Org profile: [profile/README.md](./README.md)
