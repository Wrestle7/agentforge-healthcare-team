# Offline browser dependencies

These browser distributions are pinned, committed with their upstream licenses,
and loaded from the same origin. They do not contact a CDN at runtime.

| Dependency | Version | Upstream browser distribution |
| --- | --- | --- |
| markdown-it | 15.0.2 | `https://cdn.jsdelivr.net/npm/markdown-it@15.0.2/dist/browser/markdown-it.umd.min.js` |
| DOMPurify | 3.4.15 | `https://cdn.jsdelivr.net/npm/dompurify@3.4.15/dist/purify.min.js` |

Licenses: `markdown-it-LICENSE.txt` (MIT), `dompurify-LICENSE.txt` (Apache-2.0 OR MPL-2.0).
See `SHA256SUMS.txt` for the downloaded files' integrity hashes. Upgrade deliberately
and rerun the frontend security and rendering tests after any change.
