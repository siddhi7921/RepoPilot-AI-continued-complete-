# RepoPilot AI 🚀

### Evidence-Driven Repository Investigation & AI-Assisted Code Repair

**Investigate. Understand. Propose. Verify.**

RepoPilot AI is an AI-powered developer tool that investigates public GitHub repositories, traces issues to source-code evidence, generates reviewable patch proposals, and reports verification results transparently.

Instead of blindly trusting AI-generated fixes, RepoPilot connects its analysis to retrieved source evidence, validates proposed changes, and keeps humans in control of patch approval.

[Features](#-features) · [Architecture](#-architecture) · [Getting Started](#-getting-started) · [Deployment](#-deployment) · [Limitations](#-limitations)

---

## 🌟 Why RepoPilot AI?

Debugging unfamiliar repositories can require hours of reading source code, tracing dependencies, reproducing issues, and evaluating possible fixes.

RepoPilot AI aims to streamline this workflow by combining repository inspection, structured AI reasoning, source citations, and human-reviewed patch proposals.

**Core principles:**

- 🔎 **Evidence first:** Ground analysis in retrieved repository files and source references.
- 📌 **Commit-pinned context:** Associate evidence with a specific source revision.
- 🧠 **Structured AI analysis:** Organize findings into actionable investigation reports.
- 🛠️ **Reviewable patches:** Generate candidate changes for human approval.
- 🛡️ **Safe by design:** Never modify the original GitHub repository.
- ✅ **Honest verification:** Clearly distinguish successful checks from blocked or unexecuted tests.

## ✨ Features

### 🔍 Repository Investigation
- Investigate accessible public GitHub repositories.
- Retrieve relevant source files and associate evidence with a specific commit.
- Apply bounded repository ingestion to keep analysis manageable.
- Identify when indexing is partial and communicate that limitation.

### 🧠 Evidence-Grounded AI Analysis
- Analyze repository issues using structured AI outputs.
- Produce root-cause hypotheses supported by retrieved source evidence.
- Validate source citations against the available evidence.
- Explain when evidence is insufficient or an AI provider fails.

### 🩹 AI-Assisted Patch Proposals
- Generate candidate unified diffs for review.
- Validate proposed changes against retrieved source evidence.
- Apply approved candidate changes to a separate source snapshot.
- Preserve the original repository without silently pushing or modifying upstream code.

### 📋 Transparent Verification
RepoPilot distinguishes between actual verification results and work that could not be executed.

| Status | Meaning |
|---|---|
| `PASS` | The relevant check completed successfully. |
| `FAIL` | The check ran and failed. |
| `NOT_RUN` | The check was not executed. |
| `BLOCKED` | Verification could not proceed because a required capability or dependency was unavailable. |

A generated patch is not automatically a verified fix. On the Netlify deployment, Python test execution is blocked because an isolated execution worker is not included.

### 🔐 Authentication & Data Isolation
- Netlify Identity supports account registration and authentication flows.
- Email confirmation is required by default.
- Investigations are associated with authenticated accounts.
- API queries enforce investigation ownership.
- Persistent investigation records use Netlify Database with Drizzle ORM.

### ⚡ Modern Deployment Architecture
- Netlify-hosted frontend and serverless API functions.
- Netlify AI Gateway for AI analysis.
- Database-backed investigation persistence.
- Environment-based server-side GitHub token configuration.
- Existing Python/Docker application directories retained for legacy installations.

## 🏗️ Architecture

The repository supports two application paths.

### Netlify application

\`\`\`text
RepoPilot AI
├── public/              # Web interface and generated assets
├── web/                 # Frontend source, including Identity integration
├── server/              # Shared server-side application logic
├── netlify/
│   └── functions/       # Serverless API endpoints
├── db/                  # Database schema and migrations
├── netlify.toml         # Netlify build and routing configuration
├── package.json
└── package-lock.json
\`\`\`

**Investigation workflow**

1. Authenticate the user.
2. Retrieve bounded source evidence from a public GitHub repository.
3. Run structured AI analysis using Netlify AI Gateway.
4. Validate citations and candidate patch changes.
5. Store investigation results and the separate source snapshot.
6. Present the report and accurately describe verification status.

### Legacy Python application

The original Python application directories remain available for existing Docker-based installations. They can continue to use the Python application's own dependencies and test suite.

The Netlify deployment does not run the Python server or execute repository Python tests.

## 🧰 Technology Stack

| Layer | Technology |
|---|---|
| Web deployment | Netlify |
| Serverless API | Netlify Functions |
| Frontend | TypeScript and browser assets |
| Database access | Drizzle ORM |
| Persistent storage | Netlify Database |
| Authentication | Netlify Identity |
| AI integration | Netlify AI Gateway |
| Legacy backend | Python, FastAPI and Docker |
| Source control | Git and GitHub |

## 🚀 Getting Started

### Prerequisites

- Node.js 22.12 or later
- npm
- A Git client
- A Netlify account for hosted deployment
- Required Netlify database and AI Gateway capabilities for production use

### 1. Clone the repository

\`\`\`bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
\`\`\`

Replace the repository URL with the actual RepoPilot AI GitHub URL.

### 2. Install dependencies

\`\`\`bash
npm ci
\`\`\`

### 3. Build the application

\`\`\`bash
npm run build
\`\`\`

The Netlify build generates `public/identity.js` from `web/identity.ts`.

### 4. Run the development checks

\`\`\`bash
npm run typecheck
npm test
\`\`\`

### 5. Run with Netlify Dev

\`\`\`bash
netlify dev --port 8889
\`\`\`

Run the commands individually and review each exit code. Local execution may require Netlify authentication, database configuration, or AI Gateway access.

## ☁️ Deployment

1. Push the repository to GitHub.
2. Import the repository into Netlify.
3. Confirm that the root `netlify.toml` is used.
4. Ensure the configured database and AI Gateway are available to the deployed application.
5. Configure any required server-side GitHub token through the deployment environment.
6. Deploy and inspect the build logs.
7. Test registration, authentication, investigation creation, persistence, report retrieval, and ownership enforcement.

The checked-in database schema and migration files define the application's database setup. Confirm that the deployed database is provisioned and migrations have completed successfully before using the application.

Netlify hosts the web application and serverless API. It does not host the persistent FastAPI process or provide the isolated Python test worker required for repository test execution.

## ⚙️ Repository Ingestion Limits

The Netlify application uses bounded ingestion to control request size and analysis cost.

| Limit | Value |
|---|---:|
| Source files per investigation | 80 |
| Maximum size per file | 64 KB |
| Total indexed source size | 3 MB |

Reports should clearly indicate when the available source snapshot is partial. These limits do not imply that every file in a repository has been inspected.

## 🔒 Security

RepoPilot AI is designed around reviewable changes and separation between investigation and modification.

- The original upstream repository is not modified.
- Candidate changes are applied to a separate source snapshot.
- Investigation access is restricted to the owning authenticated account.
- API keys and tokens must remain server-side.
- Repository content and AI-generated output must be treated as untrusted input.
- Running arbitrary repository code requires a separate, strongly isolated execution environment.

**Important:** These design goals do not replace a security audit. Review deployed configuration, authorization, request validation, dependency vulnerabilities, and execution isolation before handling sensitive workloads.

## 🧪 Testing

Run the Node.js checks from the repository root:

\`\`\`bash
npm ci
npm run build
npm run typecheck
npm test
\`\`\`

The legacy Python test suite can also be run after installing the appropriate requirements for the original Python application.

Its Git patch integration test requires an environment where `git apply` is available and permitted.

Test results should be reported from actual command output. A successful frontend build does not prove that every API endpoint, database migration, AI integration, or patch workflow works correctly.

## ⚠️ Deployment requirements and remaining constraints

Repository ingestion now prioritizes issue-related paths and tests, supports an optional `include_paths` array in investigation requests, and records coverage counts, oversized files, truncated trees, and missing requested paths. The source snapshot remains bounded to 80 files, 64 KB per file, and 3 MB total. Configure `GITHUB_TOKEN` server-side for a higher GitHub request quota; GitHub still enforces its own rate limits.

Candidate patches must apply cleanly and pass a separate AI review before being offered. Findings remain hypotheses, and neither citations, AI review, nor passing tests guarantees correctness. Provider diagnostics now perform a small inference request through AI Gateway rather than checking only model metadata. Production access and account entitlements still require a successful deployed diagnostic.

### Connect the Python runner

The verification endpoint now calls the existing runner over authenticated HTTPS and binds each result to a hash of the exact baseline or approved source snapshot. Approving a patch clears earlier verification results. Unavailable or invalid runner responses remain `BLOCKED`; test output is not persisted because repository output may contain sensitive data.

Deploy the dedicated configuration in `deploy/runner/compose.yaml` on the runner host. Configure `RUNNER_DOMAIN` and a strong `REPOPILOT_RUNNER_TOKEN` through that host's secret management, then start the services with Docker Compose. Configure the matching token and HTTPS `REPOPILOT_RUNNER_URL` in the Netlify function environment. The gateway authenticates requests and strips authorization before forwarding to the internal runner. Keep application credentials and databases off the runner host. Review its execution isolation before accepting untrusted repositories.

The runner deployment and an end-to-end deployed verification have not been performed. Netlify Functions delegate Python execution to this separately hosted worker.

### Migrate legacy SQLite data

The administrator tool is `scripts/migrate_legacy.py`. Keep the Python application operational and retain the original database until migration results have been verified.

1. Create a private working directory outside source control. Run `python3 scripts/migrate_legacy.py prepare --sqlite /path/to/original.sqlite --backup /private/path/backup.sqlite --export /private/path/export.migration.json`. Preparation uses SQLite's online backup, verifies backup integrity, and exports account metadata and reports without passwords or sessions. Existing backup and export files are never overwritten.
2. Create a private JSON mapping from each legacy username to its explicitly verified Netlify Identity user ID. Each destination must exist and be confirmed. Account matching by email or username is not performed. Unowned legacy reports are skipped for manual review.
3. Sign in as a confirmed Identity administrator with the server-controlled `admin` role. Supply the current session through `REPOPILOT_ADMIN_TOKEN` in the process environment without putting it in command history or logs. The tool sends this session as a secure HTTPS cookie; it never imports legacy sessions.
4. Run `python3 scripts/migrate_legacy.py import --export /private/path/export.migration.json --mapping /private/path/mapping.json --site https://your-site.example`. This performs a dry run and prints counts, skipped record indexes, and a confirmation digest without personal data.
5. Review the complete dry-run summary. Repeat the same command with `--commit --confirm-digest DIGEST_FROM_DRY_RUN` to commit the reviewed plan. Batches are transactional and audited; rerunning the same export detects duplicates. Conflicting IDs, changed imports, or ownership changes stop the import without overwriting reports. Reduce `--batch-size` if necessary; the default is one record.
6. Review the verified report counts and all skipped records. Imported reports retain supported IDs, timestamps, evidence, source snapshots, and history. Previous approvals and verification claims are preserved only as historical metadata; users sign in again and repeat review and verification.

The new database migration must deploy before importing. No real legacy database has been imported or verified. Backup files contain the original database and must be protected as sensitive data. Full live-database migration, concurrency, and rollback validation remain necessary before production use.

## 🗺️ Roadmap

Potential future improvements include:

- [ ] A hardened, isolated test-execution worker.
- [ ] Automated regression testing for generated patches.
- [ ] Richer dependency and cross-file analysis.
- [ ] Investigation history and improved report comparison.
- [ ] Additional repository providers and source-control integrations.
- [ ] More comprehensive security and performance testing.
- [ ] Expanded CI checks and deployment health monitoring.

Roadmap items are proposals, not claims of existing functionality.

## 🤝 Contributing

Contributions, bug reports, documentation improvements, and security-focused reviews are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Make a focused change.
4. Run the relevant tests and checks.
5. Submit a pull request explaining the change and its validation.

Please never include API keys, access tokens, private repository contents, or production data in commits or issue reports.

## 📄 License

Add the project's actual license here. If no license has been selected, choose one before describing the repository as open source.

---

**RepoPilot AI — Evidence before assumptions. Review before applying. Verify before trusting.**
