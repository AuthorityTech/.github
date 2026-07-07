#!/usr/bin/env node
/**
 * validate-internal-links.mjs — portable, zero-dependency CI gate.
 *
 * Fails (exit 1) when any published content file links to a same-site CONTENT
 * route that does not resolve. A link is VALID when it (a) maps to a real
 * published content file, (b) matches a configured REDIRECT source (the route
 * was renamed/moved and the server 301s it — NOT a dead link), or (c) is the
 * `.md` machine-readable mirror of a valid route. Only links that satisfy none
 * of these are dead. This is the post-push backstop for the failure class the
 * editorial publisher's internal-link-guard already handles pre-push.
 *
 * Design (judo, not force): the gate is scoped to *content lane prefixes*
 * (`/blog/`, `/curated/`, `/glossary/`, `/research/`, `/industries/`, ...) declared
 * in `internal-links.config.json`. A link is validated only when its pathname falls
 * under a configured lane prefix; everything else (static pages, anchors, externals)
 * is out of scope. There is NO site-wide route table to keep current — valid routes
 * are derived from the content files themselves AND the site's own redirect config,
 * so the gate is self-maintaining as content is renamed, added, or removed. The same
 * config drives the `--heal` pass: a link to renamed content is REWRITTEN to follow
 * the redirect to its live destination (auto-update), and only a genuinely dead link
 * (no file, no redirect) is stripped.
 *
 * Identical byte-for-byte across every content repo. The canonical copy + its
 * regression lock live in AuthorityTech/jarvis (jarvis-runtime/). Vendored into each
 * content repo at scripts/validate-internal-links.mjs with a sibling config.
 *
 * Config shape (internal-links.config.json, repo root):
 * {
 *   "siteHost": "authoritytech.io",
 *   "lanes": [
 *     { "dir": "content/blog", "prefix": "/blog", "stripDate": true },
 *     { "dir": "content/industries", "prefix": "/industries", "nested": true }
 *   ],
 *   "mdMirror": true,          // also accept `<route>.md` machine-readable mirrors
 *   "allowlist": ["/blog/some-legacy-slug"],  // optional explicit valid paths
 *   "redirects": [             // optional; defaults to auto-detecting the files below
 *     { "file": "next.config.js", "type": "next" },
 *     { "file": "vercel.json", "type": "vercel" }
 *   ]
 * }
 *
 * Redirect parsing is read-only and code-free: Next configs are scanned for
 * `source`/`destination` string-literal pairs inside the `redirects()` block (never
 * executed); Vercel configs are JSON-parsed for `redirects[]`. Static and
 * parameterized (`:slug`) sources are both supported.
 *
 * Lane options:
 *   dir        content directory, relative to repo root (recursed)
 *   prefix     URL prefix the lane serves under (e.g. "/blog")
 *   stripDate  strip a leading YYYY-MM-DD- from the filename when deriving the slug
 *   nested     keep sub-directory structure in the route (e.g. industries/<a>/<b>)
 *   ext        file extensions to treat as content (default ["md","mdx"])
 *   slugFromFrontmatter  honor a frontmatter `slug:` override (default true). Set
 *              false for lanes whose site builder derives the slug from the filename
 *              ONLY and ignores frontmatter (the satellite blogs do this).
 * Frontmatter `slug:` overrides the derived slug exactly as the site builder does,
 * so the gate computes the same route the site serves — never a divergent one.
 *
 * Usage:
 *   node scripts/validate-internal-links.mjs            # gate: exit 1 on dead links
 *   node scripts/validate-internal-links.mjs --heal     # rewrite files, fix/strip links
 *   node scripts/validate-internal-links.mjs --json     # machine-readable report
 */
import fs from 'node:fs';
import path from 'node:path';

const MARKDOWN_LINK = /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
const HTML_ANCHOR = /<a\b[^>]*?\bhref=(["'])([^"']+)\1[^>]*>([\s\S]*?)<\/a>/gi;
const DATE_PREFIX = /^\d{4}-\d{2}-\d{2}-/;
const REDIRECT_MAX_HOPS = 10;

function readConfig(root) {
  const p = path.join(root, 'internal-links.config.json');
  if (!fs.existsSync(p)) {
    throw new Error(`internal-links.config.json not found at ${p}`);
  }
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

/** Minimal frontmatter slug reader — no YAML dep; only the slug field is needed. */
function frontmatterSlug(raw) {
  if (!raw.startsWith('---')) return null;
  const end = raw.indexOf('\n---', 3);
  if (end === -1) return null;
  const block = raw.slice(3, end);
  const m = block.match(/^\s*slug:\s*["']?([^"'\n#]+?)["']?\s*$/m);
  return m ? m[1].trim() : null;
}

function listFiles(dir, exts) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFiles(full, exts));
    else if (exts.some((e) => entry.name.endsWith(`.${e}`))) out.push(full);
  }
  return out;
}

/** Derive the canonical route path for a content file under a lane. */
function routeForFile(root, lane, file, raw) {
  const exts = lane.ext || ['md', 'mdx'];
  const rel = path.relative(path.join(root, lane.dir), file);
  let stem = rel.replace(new RegExp(`\\.(${exts.join('|')})$`), '');
  const fmSlug = lane.slugFromFrontmatter === false ? null : frontmatterSlug(raw);
  if (fmSlug) {
    // Frontmatter slug overrides; nested lanes keep the parent dirs, flat lanes do not.
    if (lane.nested) {
      const dirPart = path.dirname(stem);
      stem = dirPart === '.' ? fmSlug : `${dirPart}/${fmSlug}`;
    } else {
      stem = fmSlug;
    }
  } else if (lane.nested) {
    if (lane.stripDate) {
      stem = stem.split('/').map((s) => s.replace(DATE_PREFIX, '')).join('/');
    }
  } else {
    stem = path.basename(stem);
    if (lane.stripDate) stem = stem.replace(DATE_PREFIX, '');
  }
  const prefix = lane.prefix.replace(/\/+$/, '');
  return `${prefix}/${stem}`;
}

/** Normalize a link URL to a same-host root-relative pathname, or null if off-site. */
function sameSitePathname(url, siteHost) {
  const u = url.trim();
  let pathname;
  if (u.startsWith('/')) {
    pathname = u;
  } else if (/^https?:\/\//i.test(u)) {
    let parsed;
    try { parsed = new URL(u); } catch { return null; }
    if (parsed.hostname !== siteHost) return null;
    pathname = parsed.pathname;
  } else {
    return null; // mailto:, #anchor, relative fragment, etc.
  }
  return pathname.split(/[?#]/)[0].replace(/\/+$/, '') || '/';
}

/* ----------------------------- Redirect model ----------------------------- */

/** Turn a Next/Vercel path pattern into a matcher. `:name` matches one segment. */
function sourceToMatcher(source) {
  const src = source.replace(/\/+$/, '') || '/';
  const paramNames = [];
  // Escape regex specials first (turns * -> \*, + -> \+, leaves : intact), then
  // expand :params. This way literal dots/parens in slugs can never break out.
  let regex = src.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  regex = regex.replace(/:([A-Za-z0-9_]+)(\\\*|\\\+)?/g, (_m, name, mod) => {
    paramNames.push(name);
    if (mod === '\\*') return '(.*)';
    if (mod === '\\+') return '(.+)';
    return '([^/]+)';
  });
  return { regex: new RegExp(`^${regex}$`), paramNames };
}

/** Extract source/destination pairs from the redirects() block of a Next config. */
function parseNextRedirects(raw) {
  const start = raw.indexOf('redirects()');
  if (start === -1) return [];
  let block = raw.slice(start);
  const rewrites = block.indexOf('rewrites()');
  if (rewrites !== -1) block = block.slice(0, rewrites); // never treat rewrites as redirects
  const out = [];
  const re = /source:\s*(["'`])([^"'`]+)\1[\s\S]*?destination:\s*(["'`])([^"'`]+)\3/g;
  let m;
  while ((m = re.exec(block))) out.push({ source: m[2], destination: m[4] });
  return out;
}

/** Extract redirects[] from a Vercel config. */
function parseVercelRedirects(raw) {
  let j;
  try { j = JSON.parse(raw); } catch { return []; }
  return (j.redirects || [])
    .filter((r) => r && r.source && r.destination)
    .map((r) => ({ source: r.source, destination: r.destination }));
}

/** Load + compile the site's redirect table from config (or auto-detected files). */
function loadRedirects(root, config) {
  const specs = config.redirects || [
    { file: 'next.config.js', type: 'next' },
    { file: 'next.config.mjs', type: 'next' },
    { file: 'vercel.json', type: 'vercel' },
  ];
  const entries = [];
  for (const spec of specs) {
    const p = path.join(root, spec.file);
    if (!fs.existsSync(p)) continue;
    const raw = fs.readFileSync(p, 'utf8');
    const parsed = spec.type === 'vercel' ? parseVercelRedirects(raw) : parseNextRedirects(raw);
    entries.push(...parsed);
  }
  return entries.map((e) => ({ ...e, ...sourceToMatcher(e.source) }));
}

/** First redirect whose source matches `pathname`, with params substituted in. */
function redirectMatch(pathname, redirects) {
  for (const r of redirects) {
    const m = pathname.match(r.regex);
    if (!m) continue;
    let dest = r.destination;
    r.paramNames.forEach((name, i) => { dest = dest.split(`:${name}`).join(m[i + 1] ?? ''); });
    return { destination: dest };
  }
  return null;
}

/**
 * Follow a redirect chain to its terminal target. Returns null when `pathname` is
 * not a redirect source. Otherwise { terminal, external } where `terminal` is a
 * same-site pathname (external=false) or a full off-site URL (external=true).
 */
function resolveRedirect(pathname, redirects, siteHost, depth = 0) {
  if (depth > REDIRECT_MAX_HOPS) return { terminal: pathname, external: false };
  const hit = redirectMatch(pathname, redirects);
  if (!hit) return null;
  let dest = hit.destination.split('#')[0].trim();
  if (/^https?:\/\//i.test(dest)) {
    const sp = sameSitePathname(dest, siteHost);
    if (sp == null) return { terminal: dest.replace(/\/+$/, '') || dest, external: true };
    dest = sp;
  } else {
    dest = dest.replace(/\/+$/, '') || '/';
  }
  const further = resolveRedirect(dest, redirects, siteHost, depth + 1);
  return further || { terminal: dest, external: false };
}

/* ------------------------------- Validation ------------------------------- */

/** In-scope = under a content lane prefix, but not the bare lane index itself. */
function inScope(pathname, lanePrefixes) {
  return pathname != null
    && lanePrefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`))
    && !lanePrefixes.includes(pathname);
}

/** Does this same-site pathname map to a real published content file (or its .md mirror)? */
function resolvesContent(pathname, validPaths) {
  if (pathname == null) return false;
  const c = pathname.endsWith('.md') ? pathname.slice(0, -3) : pathname;
  return validPaths.has(c) || validPaths.has(pathname);
}

/**
 * Resolve an in-scope pathname to what it actually reaches:
 *   content          -> a real content file
 *   redirect-internal-> 301 chain terminates at a real internal content file (`target`)
 *   redirect-external-> 301 chain terminates off-site (`target` = full URL)
 *   dead             -> nothing reachable (no file; or a 301 that lands on a 404)
 * A redirect whose terminal is internal-but-nonexistent is DEAD: the server 301s,
 * then 404s. That is a broken link, not a valid one.
 */
function resolveTarget(pathname, validPaths, redirects, siteHost) {
  if (resolvesContent(pathname, validPaths)) return { status: 'content', target: pathname };
  const term = resolveRedirect(pathname, redirects, siteHost);
  if (term) {
    if (term.external) return { status: 'redirect-external', target: term.terminal };
    if (resolvesContent(term.terminal, validPaths)) return { status: 'redirect-internal', target: term.terminal };
  }
  return { status: 'dead' };
}

/** Is this same-site pathname an in-scope CONTENT route that does not resolve? */
function deadContentPath(pathname, lanePrefixes, validPaths, redirects = [], siteHost = null) {
  if (!inScope(pathname, lanePrefixes)) return false;
  return resolveTarget(pathname, validPaths, redirects, siteHost).status === 'dead';
}

function buildModel(root, config) {
  const lanePrefixes = config.lanes.map((l) => l.prefix.replace(/\/+$/, ''));
  const validPaths = new Set(config.allowlist || []);
  const files = [];
  for (const lane of config.lanes) {
    const exts = lane.ext || ['md', 'mdx'];
    for (const file of listFiles(path.join(root, lane.dir), exts)) {
      const raw = fs.readFileSync(file, 'utf8');
      const route = routeForFile(root, lane, file, raw);
      validPaths.add(route);
      files.push({ file, raw });
    }
  }
  const redirects = loadRedirects(root, config);
  return { lanePrefixes, validPaths, files, redirects };
}

function scanBody(raw, siteHost, lanePrefixes, validPaths, redirects = []) {
  const body = raw.startsWith('---')
    ? raw.slice(Math.max(0, raw.indexOf('\n---', 3)) + 4)
    : raw;
  const dead = [];
  const check = (url, anchor, kind) => {
    const pathname = sameSitePathname(url, siteHost);
    if (deadContentPath(pathname, lanePrefixes, validPaths, redirects, siteHost)) {
      dead.push({ url: url.trim(), pathname, anchor, kind });
    }
  };
  for (const m of body.matchAll(MARKDOWN_LINK)) check(m[2], m[1], 'markdown');
  for (const m of body.matchAll(HTML_ANCHOR)) check(m[2], m[3], 'html');
  return dead;
}

/**
 * Heal a same-site link. Order of resolution mirrors the server:
 *   - resolves to a real content file        -> keep (valid)
 *   - renamed/moved (matches a redirect)      -> REWRITE to the live destination
 *                                                (follow the 301 chain to terminal);
 *                                                cross-domain moves become the full URL
 *   - date-orphan whose dateless route exists -> REPAIR to the dateless URL
 *   - genuinely dead (none of the above)      -> STRIP, collapse to anchor text
 * Returns { action: 'keep' | 'rewrite' | 'strip', to? }.
 */
function classifyLink(url, siteHost, lanePrefixes, validPaths, redirects) {
  const pathname = sameSitePathname(url, siteHost);
  if (!inScope(pathname, lanePrefixes)) return { action: 'keep' };
  const r = resolveTarget(pathname, validPaths, redirects, siteHost);
  if (r.status === 'content') return { action: 'keep' };
  const tail = (url.match(/[?#].*$/) || [''])[0];
  // Renamed/moved content — follow the 301 chain and auto-update the link.
  if (r.status === 'redirect-external') return { action: 'rewrite', to: r.target };
  if (r.status === 'redirect-internal') return { action: 'rewrite', to: r.target + tail };
  // Dead. Last chance: a `/YYYY-MM-DD-` date-orphan whose dateless route resolves.
  const candidate = url.replace(/\/\d{4}-\d{2}-\d{2}-/, '/');
  if (candidate !== url && resolvesContent(sameSitePathname(candidate, siteHost), validPaths)) {
    return { action: 'rewrite', to: candidate };
  }
  return { action: 'strip' };
}

function healBody(raw, siteHost, lanePrefixes, validPaths, redirects = []) {
  const repaired = [];
  const stripped = [];
  const apply = (url, collapse) => {
    const c = classifyLink(url, siteHost, lanePrefixes, validPaths, redirects);
    if (c.action === 'rewrite') {
      repaired.push({ from: url.trim(), to: c.to.trim() });
      return collapse(c.to);
    }
    if (c.action === 'strip') {
      stripped.push(url.trim());
      return null; // signal: collapse to text
    }
    return undefined; // keep
  };
  let out = raw.replace(MARKDOWN_LINK, (match, text, url) => {
    const res = apply(url, (fixed) => match.replace(url, () => fixed));
    if (res === undefined) return match;
    return res === null ? text : res;
  });
  out = out.replace(HTML_ANCHOR, (match, _q, url, inner) => {
    const res = apply(url, (fixed) => match.replace(url, () => fixed));
    if (res === undefined) return match;
    return res === null ? inner : res;
  });
  return { out, repaired, stripped };
}

function main() {
  const root = process.cwd();
  const heal = process.argv.includes('--heal');
  const json = process.argv.includes('--json');
  const config = readConfig(root);
  const siteHost = config.siteHost;
  if (!siteHost) throw new Error('config.siteHost is required');
  const { lanePrefixes, validPaths, files, redirects } = buildModel(root, config);

  const report = [];
  let repairedCount = 0;
  let strippedCount = 0;
  for (const { file, raw } of files) {
    if (heal) {
      // Heal must not touch frontmatter; split, heal body, rejoin.
      const fmEnd = raw.startsWith('---') ? raw.indexOf('\n---', 3) : -1;
      const head = fmEnd === -1 ? '' : raw.slice(0, fmEnd + 4);
      const body = fmEnd === -1 ? raw : raw.slice(fmEnd + 4);
      const { out, repaired, stripped } = healBody(body, siteHost, lanePrefixes, validPaths, redirects);
      if (repaired.length || stripped.length) {
        fs.writeFileSync(file, head + out);
        repairedCount += repaired.length;
        strippedCount += stripped.length;
        report.push({ file: path.relative(root, file), repaired, stripped });
      }
    } else {
      const dead = scanBody(raw, siteHost, lanePrefixes, validPaths, redirects);
      if (dead.length) report.push({ file: path.relative(root, file), dead });
    }
  }

  if (json) {
    console.log(JSON.stringify({ mode: heal ? 'heal' : 'check', report }, null, 2));
  }

  if (heal) {
    if (!json) {
      console.log(
        `internal-links: healed ${report.length} file(s) — ` +
        `rewrote ${repairedCount} link(s) to live route, stripped ${strippedCount} dead link(s).`
      );
    }
    return;
  }

  const total = report.reduce((n, r) => n + r.dead.length, 0);
  if (total === 0) {
    if (!json) console.log(`internal-links: OK — no dangling same-site content links (${files.length} files scanned).`);
    process.exit(0);
  }
  if (!json) {
    console.error(`internal-links: FAIL — ${total} dangling same-site content link(s):\n`);
    for (const r of report) {
      console.error(`  ${r.file}`);
      for (const d of r.dead) console.error(`    [${d.kind}] ${d.url}  ->  ${d.pathname}`);
    }
    console.error(`\nFix: remove the dead link(s), or republish so the publisher guard strips them.`);
  }
  process.exit(1);
}

// Export the pure pieces for the regression lock; run only as a CLI entrypoint.
export {
  routeForFile, sameSitePathname, deadContentPath, scanBody, healBody, buildModel,
  frontmatterSlug, sourceToMatcher, parseNextRedirects, parseVercelRedirects,
  redirectMatch, resolveRedirect, classifyLink, loadRedirects,
  inScope, resolvesContent, resolveTarget,
};

if (import.meta.url === `file://${process.argv[1]}`) main();
