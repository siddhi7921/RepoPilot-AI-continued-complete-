# RepoPilot AI on Netlify

RepoPilot investigates public GitHub repositories using commit-pinned source evidence, structured AI analysis, citation validation, and human-approved candidate patches. The Netlify application lives in `public/`, `server/`, `netlify/functions/`, and `db/`. The two original Python application directories remain available for existing Docker installations.

## Deployment

Connect this repository to Netlify. The root `netlify.toml` configures the publish directory, browser Identity bundle, API function, headers, and investigation detail URLs. Dependency installation uses `package-lock.json`.

Investigations persist in Netlify Database through Drizzle ORM. The initial schema and generated migration are checked in; Netlify applies migrations during deployment. No SQLite file or persistent application process is required for the Netlify application.

Netlify Identity provides email registration, confirmation, sign-in, password recovery, and invitation acceptance. Registration requires email confirmation by default. Each investigation belongs to its authenticated account, and API queries enforce ownership. Previous Python usernames and sessions do not transfer to Identity; users create an email account on the Netlify site. Existing SQLite reports are not automatically imported.

AI calls use Netlify AI Gateway with `gpt-5.2`. No user-supplied model key is needed. If analysis cannot complete, the investigation still saves real source evidence and explains the failure. The included session-expiry sample already contains the corrected `>=` guard; analysis must not invent an expiration bug or unnecessary patch.

GitHub requests work without a token, subject to GitHub's public rate limits. An existing server-side `GITHUB_TOKEN` configuration is supported. Repository ingestion is bounded to 24 source files, 64 KB per file, and 768 KB total. Partial indexing is labeled in the report.

Patch approval validates a unified diff against retrieved evidence and applies it to a separate source snapshot stored with the investigation. The original repository is never modified. Python tests are **not executed** on Netlify Functions: verification reports `BLOCKED` because this deployment has no isolated execution worker. It never reports an unexecuted test as passed.

## Development and checks

Use Node.js 22.12 or later.

```sh
npm ci
npm run build
netlify dev --port 8889
npm run typecheck
npm test
```

Netlify generates `public/identity.js` during its build. The source is `web/identity.ts`.

The original Python suite can also be run from the repository root after installing the primary application's requirements. Its real Git patch integration test requires an environment that permits `git apply`.
