/**
 * Every `type-*`, `rounded-*`, and semantic colour class in `src/` resolves to
 * something `globals.css` actually defines.
 *
 * Tailwind v4 is CSS-first here: there is no config file, and an unknown
 * utility is not an error -- it emits nothing, and the element silently renders
 * at the browser default. `type-heading`, `type-subheading`, `type-caption`,
 * and `border-border` sat in three files that way, left over from before the
 * Section refactor, and nothing noticed because nothing could.
 *
 * Scoped deliberately to the families this design system owns, where a typo is
 * invisible. Tailwind's own built-ins (`border-t`, `text-sm`) are not our
 * business.
 *
 * Run: node scripts/validate-design-tokens.mjs
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const CSS = "src/app/globals.css";
const SRC = "src";

const css = readFileSync(CSS, "utf8");

const utilities = new Set([...css.matchAll(/@utility\s+([a-z0-9-]+)/g)].map((m) => m[1]));
const colorNames = new Set([...css.matchAll(/--color-([a-z0-9-]+)\s*:/g)].map((m) => m[1]));
const radiusNames = new Set([...css.matchAll(/--radius-([a-z0-9-]+)\s*:/g)].map((m) => m[1]));

/** Built-in radii: using one means stepping outside the four-radius system. */
const TAILWIND_RADII = new Set(["none", "sm", "md", "lg", "xl", "2xl", "3xl"]);

const files = [];
(function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(tsx?|css)$/.test(path) && path !== CSS) files.push(path);
  }
})(SRC);

const problems = [];

for (const file of files) {
  readFileSync(file, "utf8")
    .split("\n")
    .forEach((line, i) => {
      const report = (cls, hint) => problems.push(`${file}:${i + 1}  ${cls}  -- ${hint}`);

      // `type-*` is ours entirely: every one must be an @utility.
      for (const [, name] of line.matchAll(/\btype-([a-z0-9-]+)/g)) {
        if (!utilities.has(`type-${name}`)) {
          report(`type-${name}`, `no @utility type-${name} in ${CSS}`);
        }
      }

      // `rounded-full` (pills, avatars) and `rounded-*-none` (removing a corner
      // on one side, as a sheet does where it meets an edge) are legitimate.
      // The numbered scale is what steps outside the system.
      for (const [, name] of line.matchAll(/\brounded-(?:[trbl]{1,2}-)?([a-z0-9-]+)/g)) {
        if (radiusNames.has(name) || name === "full" || name === "none") continue;
        if (TAILWIND_RADII.has(name)) {
          report(
            `rounded-${name}`,
            `outside the four-radius system (${[...radiusNames].sort().join(", ")})`,
          );
        }
      }

      // A colour name shaped like ours but undefined. `border-border` is the
      // shape of this mistake: the suffix repeats the utility family.
      for (const [, prefix, name] of line.matchAll(
        /\b(border|bg|text|fill|stroke|ring|accent)-([a-z][a-z0-9-]*)/g,
      )) {
        if (colorNames.has(name)) continue;
        if (name === prefix || [...colorNames].some((c) => c.startsWith(`${name}-`))) {
          report(`${prefix}-${name}`, `no --color-${name} in ${CSS}`);
        }
      }
    });
}

if (problems.length) {
  console.error(`Design tokens that resolve to nothing (${problems.length}):\n`);
  for (const p of problems) console.error(`  ${p}`);
  console.error(
    `\nTailwind v4 emits no CSS for an unknown utility, so these render at the ` +
      `browser default rather than failing. Map each onto the scale in ${CSS}.`,
  );
  process.exit(1);
}

console.log(
  `Design tokens OK -- ${files.length} files, ${utilities.size} utilities, ${colorNames.size} colours.`,
);
