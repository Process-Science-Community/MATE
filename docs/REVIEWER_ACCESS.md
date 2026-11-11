# Anonymous reviewer access (ICPM 2027 demo review)

Five local Keycloak accounts for the demo-track reviewers. They have no email
address and no personal data. Each account is preloaded with sample logs and an
example dashboard, nothing a reviewer does is usage-tracked, and every account
sees only its own data. Access runs until **14 Dec 2026**; the accounts are
disabled from **15 Dec 2026**.

## For the reviewers

| | |
|---|---|
| Login URL | **<https://mate.uni-muenster.de/login/start?local=1>** |
| Usernames | `reviewer1` … `reviewer5`. If the realm has **Email as username** on (the uni VM does), Keycloak stores the placeholder address instead, so reviewers log in as `reviewer1@reviewers.invalid` … `reviewer5@reviewers.invalid`. The script's summary and the credentials file show the exact login name. |
| Passwords | one per account. They are not in this repository; see [Where the passwords are](#where-the-passwords-are) |
| Preloaded | 3 sample logs (Helpdesk ticketing: samples A and B, plus a simulated one) and an *Example dashboard* (process overview of sample A) |

The URL goes straight to MATE's own username + password form, not the
university login. On <https://mate.uni-muenster.de/login> the same door is the
**Login with email** button; the username works in the email field.

## How it works

| Piece | Where | What it does |
|---|---|---|
| Accounts | `infra/keycloak/create-reviewer-accounts.sh` | Uses kcadm against the running Keycloak and is idempotent. It creates `reviewer1..5` with the realm role `reviewer` and a generated password (4 × 5 random letters/digits joined by `-`, ~119 bits) (not temporary, no required actions). It sets first/last name to "Reviewer" / "N" and no email. If the realm's user profile requires an email, it sets a placeholder `reviewerN@reviewers.invalid` (non-routable, marked verified) instead, so that no profile form appears and asks a reviewer for their real address. |
| Login door | `/login/start?local=1` (`apps/web/app/(auth)/login/start/route.ts`, already existed) | Starts the sign-in without `kc_idp_hint`, so Keycloak shows its own form. The normal **Login** button still sends `KEYCLOAK_IDP_HINT`, so university users keep going straight to SSO. |
| Tracking exemption | `apps/api/src/mate/api/services/tracking_exemption.py` | Applies even under `USER_TRACKING_ONBOARDING=force`. An account is exempt if it holds a role from `USER_TRACKING_EXEMPT_ROLES` (default `reviewer`), is listed in `USER_TRACKING_EXEMPT_USERS`, or carries the persisted `analytics.exempt` flag. Nothing is recorded for an exempt account: no client events, request events, job outcomes or MCP audit. `GET /usage/config` returns `enabled: false`, so the browser does not capture either. Tests: `apps/api/tests/test_tracking_exemption.py`. |
| Demo content | `apps/api/src/mate/api/services/sample_content.py`, `SAMPLE_CONTENT_MANIFEST` | At API start, each account in the manifest is seeded once. It imports the logs through the normal import job, so discovery precomputes eagerly, then creates the dashboard from the `process-overview` template and asks for the dashboard's other modules to compute. The script writes the manifest and the sample CSVs to `data/sample-content/reviewers/`. Tests: `apps/api/tests/test_sample_content.py`. |
| Expiry | `infra/systemd/mate-reviewer-expiry.{service,timer}`, `infra/install-reviewer-expiry.sh` | Runs `create-reviewer-accounts.sh expire` daily. It does nothing before `REVIEWER_EXPIRES` (2026-12-15). From that date it disables the accounts and ends their sessions. |
| Sandbox | `SANDBOXED_ROLES` (default `reviewer`) | Reviewers can use every installed module, but cannot upload modules (that code runs inside the API process), create a watched folder on a custom host/bucket path (only the managed per-account folder), or mint API tokens (a token is checked against MATE only, so it would outlive the disabled Keycloak account). |

`DEMO_MODE` is **not** reused. It bypasses Keycloak entirely and signs everyone
in as one shared `demo-user`, so it has no login, puts all reviewers into one
shared account, and is unsafe on a public host. Seeding solves the "preloaded"
part without it.

### Isolation

A reviewer cannot see another reviewer's data or anyone else's:

- Every log, dashboard, job, module result and setting is keyed by the Keycloak
  `sub`. A foreign id returns 404. `test_seeded_content_is_isolated` checks this
  for reviewer vs. reviewer and reviewer vs. a regular account.
- Each reviewer gets its own copy of the samples rather than a share.
- The only sanctioned cross-account read is a shared dashboard. It needs a
  common team, and only admins create teams. Reviewers are in no team, so they
  can share nothing and see no other users.
- Reviewers do not hold the `admin` role. The script removes it if someone
  grants it.
- Live events (SSE) are filtered per user server-side.
- The paths that could reach beyond the account are closed for the `reviewer`
  role (see *Sandbox* above): module upload, custom watched-folder paths, and
  API tokens.
- Routes that take a free-form `log_id` now check it before it reaches a file
  path: the module dataset routes and the AI chat context check read access
  (own or shared), and every log/result path rejects ids that are not a single
  plain path segment (no `../`). Before this change, a crafted `log_id` there
  could read another account's files. That affected every account, not only
  reviewers.

Keep it that way: do not add reviewers to a team, do not grant `admin`, and keep
`DEMO_MODE=false`.

### Sample logs

The three CSVs come from `simulated_data/input/autonomous/` in this repository.
`helpdesk-sample-a/b` are the test/train splits of the public **Helpdesk**
event log (Italian software company ticketing, 4TU.ResearchData). The simulated
one is the AgentSimulator output based on it. Before the review, confirm the
licence and attribution (believed to be CC BY 4.0) and cite it in the
submission if needed.

## Where the passwords are

On the VM only, in **`~/mate-secrets/reviewer-credentials.txt`** (mode `600`,
outside the `~/mate` checkout; `CREDENTIALS_FILE` overrides the path). The
script refuses a path inside the repository and never prints a reviewer
password or passes one on a command line (they go to Keycloak through stdin).
The Keycloak *admin* password is passed to `kcadm config credentials` as an
argument, as in the other `infra/keycloak` scripts, so it is briefly visible to
local users in `ps`. The daily expiry run only contacts Keycloak on and after the
expiry date. Send the passwords to the PC chairs through the
review system or another confidential channel, never by plain email next to
the URL.

## What you must do on the VM

The VM checkout is `~/mate`; `DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`.

1. Deploy this branch (`scripts/deploy.sh`) so the API has the seeder and the exemption.
2. Create the accounts. The `CLEAR_IDP_REDIRECTOR=1` part is explained in step 3:
   ```bash
   cd ~/mate
   KC_SERVER=http://localhost:8080/auth CLEAR_IDP_REDIRECTOR=1 \
     ./infra/keycloak/create-reviewer-accounts.sh
   ```
   The script reads `KEYCLOAK_ADMIN` and `KEYCLOAK_ADMIN_PASSWORD` from `~/mate/.env`.
3. **Check the forced IdP redirect.** Earlier runs of
   `configure-university-idp.sh` set a default provider on the browser flow's
   *Identity Provider Redirector*. With it set, Keycloak sends every sign-in to
   WWU, `local=1` included, so the reviewers could not log in. This was
   reproduced against Keycloak 26. `CLEAR_IDP_REDIRECTOR=1` deletes that config.
   University users are unaffected, because the web app still sends
   `KEYCLOAK_IDP_HINT=keycloak-oidc` on the default button. Leave the flag out
   if you only want the script to report it. `configure-university-idp.sh` no
   longer sets it unless you pass `SET_DEFAULT_PROVIDER=1`.
4. Add to `~/mate/.env`, then recreate the API (`restart` does not re-read `.env`).
   `USER_TRACKING_EXEMPT_ROLES` and `SANDBOXED_ROLES` already default to
   `reviewer`, so they need no entry:
   ```bash
   SAMPLE_CONTENT_MANIFEST=/app/data/sample-content/reviewers/manifest.json
   ```
   ```bash
   $DC up -d api
   ```
   Seeding takes about a minute. Check it with
   `$DC logs api | grep sample_content`, which should show one
   `sample_content.seeded` per reviewer.
5. Install the expiry timer:
   ```bash
   sudo ./infra/install-reviewer-expiry.sh
   ```
6. Smoke test. In a private window, open the login URL and sign in as
   `reviewer1`. You should see three logs and the *Example dashboard*. Admin →
   Insights → Usage should show no events for it.
   `./infra/keycloak/create-reviewer-accounts.sh status` (with `KC_SERVER` as
   above) lists the accounts, their roles, the expiry date and the redirect
   check.

After the review:

- `create-reviewer-accounts.sh disable` disables the accounts early.
- `enable` re-enables them, for example to extend access. Also set
  `REVIEWER_EXPIRES=YYYY-MM-DD` in `/etc/default/mate-reviewer-expiry`.
- `delete` removes the Keycloak accounts, their lines in the credentials file
  and the sample-content manifest. Then remove `SAMPLE_CONTENT_MANIFEST` from
  `.env`. Otherwise a later API start would seed the accounts again once their
  MATE data is deleted. Finally, delete the MATE data under Admin → Users.
- `sudo systemctl disable --now mate-reviewer-expiry.timer` removes the timer.
