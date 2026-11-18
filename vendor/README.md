# Locally maintained picomatch security forks

`picomatch-v2` is based on the official npm `picomatch@2.3.2` archive and
`picomatch-v4` on `picomatch@4.0.7`. Each retains its upstream MIT license and
compatible public API. The original archive integrity and parser SHA-256, and
the patched parser SHA-256, are recorded in `haiyuSecurityPatch` in each package.
The tarballs are reproducible with `npm pack --ignore-scripts` from these sources.

These are **local forks, not official fixed releases**. As of 2026-11-18,
[GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)
has no published upstream patch. The additional guard uses parsed token types
to detect an unbounded wildcard inside `+(...)` or `*(...)`, then uses the
existing upstream literal-pattern fallback instead of emitting nested repetition.
Already-safe upstream rewrites, ordinary globbing, escaped stars and bracket
characters remain supported. Repeated wildcard extglobs are intentionally
treated as literals; this is a conservative security restriction, not a rewrite
claiming to preserve every unsafe pattern's matching semantics.

Names identify the forks explicitly. The security gate also queries the actual
upstream versions against npm's advisory endpoint. It accepts only this exact
advisory after installed-source checks and subprocess attack deadlines pass;
new or changed disclosures fail the gate. Registry dependency auditing keeps
the existing high-severity threshold. No blanket advisory suppression is used.

The upstream parser also supports raw regular-expression constructs and an
explicit trusted-pattern safeguard opt-out. These forks address the named
default repeated-wildcard extglob vulnerability, not arbitrary regex safety.

When an official fix is published, replace these forks and remove the local
backport only after the attack and application regression tests pass.
