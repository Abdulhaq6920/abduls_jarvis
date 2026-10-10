# JARVIS security model

JARVIS can run commands on your PC and (soon) send email as you. The design goal is that **no
single compromised component can act on its own**: not the browser, not the backend, not the
local agent, not an LLM.

## Who is trusted with what

| Component | Trusted to | NOT trusted to |
|---|---|---|
| **You (admin sign-in)** | approve or deny actions, edit voices, read memory | n/a |
| **Backend** | route requests, queue approvals, store data | run anything on your PC |
| **Local agent (a registered device)** | execute work *you approved*, after re-checking it | approve anything itself |
| **LLM / agents** | propose actions | execute or approve anything |
| **Browser page (the dashboard)** | show data to you | hold long-lived credentials |

The central rule: **proposing, approving and executing are three different identities.**
An agent proposes. Only the `admin` role can approve. Only the `agent` (device) role can
execute. Nothing holds two of those at once.

## The path of one risky action

```
agent proposes ──► backend policy (gate 1) ──► approval queue ──► YOU approve
                     blocks DANGEROUS             (encrypted)        (admin JWT)
                                                                        │
   result reported ◄── executor ◄── LOCAL validator (gate 2) ◄── device claims it
   (spoken + audited)  no shell      re-checks everything,            (atomic: once)
                                     can refuse even an approved request
```

A grant satisfies "needs approval". It can **never** unlock a hard block (a destructive command,
a path outside your allow-list, a malformed request). That is tested.

## Threats and what addresses them

| # | Threat | Defence | Verified by |
|---|---|---|---|
| 1 | Stolen login password / brute force | argon2id hashing; 3 layered rate limits (per IP+user, user, IP); no hard account lock (it would let an attacker lock *you* out); identical responses for unknown user vs wrong password | `test_brute_force_is_throttled`, `test_login_failures_are_indistinguishable` |
| 2 | Forged or tampered token | HS256 pinned (`alg` header never trusted, `alg=none` rejected), required `iss/aud/exp/iat/nbf/sub/jti/sid/typ`, `kid` rotation | `test_alg_none_token_is_rejected`, `test_token_with_bad_claims_is_rejected` |
| 3 | Stolen access token | 15-minute lifetime; checked against live session state on **every** request, so logout/revoke is immediate, not "at expiry" | `test_logout_revokes_the_access_token_immediately` |
| 4 | Stolen refresh token | rotates on every use; replaying an old one revokes the whole token family; HttpOnly + SameSite=Strict + path-scoped cookie; original expiry is kept (no endless sliding session) | `test_refresh_rotates_the_token_and_replay_revokes_the_family` |
| 5 | XSS stealing credentials | access token lives in JS memory only (never web storage); strict CSP (no inline script/style, same-origin only); the React UI never uses `innerHTML`/`dangerouslySetInnerHTML` (lint-enforced) | `ui_smoke.py` (real browser, 0 CSP violations, no token in storage) |
| 6 | CSRF | no cookie auth on state-changing API calls (Bearer header); cookie endpoints require a matching Origin or the `X-Requested-With` header; SameSite=Strict | `test_refresh_blocks_cross_site_requests` |
| 7 | Cross-site WebSocket hijack | Origin check; token in first message (never the URL); admin-only; connection caps | `test_websocket_requires_a_valid_admin_token...` |
| 8 | DNS rebinding | `Host` header allow-list (loopback always allowed for health checks) | `test_unknown_host_header_is_refused` |
| 9 | A compromised/rogue local agent approving its own work | separate roles; the agent token gets **403** on every approval-decision route | `test_full_approval_lifecycle_with_separation_of_duties` |
| 10 | A compromised backend sending evil commands | the local validator independently re-classifies; allow-listed paths/apps are configured **on the PC** and the backend cannot widen them | `test_a_hostile_backend_cannot_make_the_local_agent_run_dangerous_things` |
| 11 | Shell injection | commands are never run through a shell: parsed to an argv, classified by exact program + allow-listed flags; any shell metacharacter is DANGEROUS; `git diff --output=x` is demoted because `--output` is not allow-listed | `tests/unit/test_commands.py` (41 cases) |
| 12 | Path spoofing (`C:\evil\notepad.exe`) | apps must be bare names on the allow-list; resolved via PATH | `test_applications_must_be_named...` |
| 13 | Replaying / double-executing an approval | atomic `UPDATE ... WHERE status=` transitions; exactly one claimant wins; grants expire (15 min) | `test_approval_claim_has_exactly_one_winner` (SQLite **and** PostgreSQL) |
| 14 | Secrets leaking to child processes | child env is scrubbed of anything secret-looking, including every `JARVIS_*` | `test_child_processes_never_inherit_secrets` |
| 15 | Secrets leaking into logs/errors | `SecretStr` everywhere; log scrubber redacts JWTs, `Bearer ...`, `password=`, and credentials inside DB/Redis URLs; 500s return an opaque request id | `scrub` check, `test_errors_do_not_leak_internals` |
| 16 | Secrets in git / images | `.env` git-ignored and docker-ignored; compose uses file-based Docker secrets; a test asserts the committed example holds no values | `test_env_template_has_no_real_secrets` |
| 17 | Stolen database / backup | conversations, memories, approvals and job output are AES-encrypted per field (Fernet, key rotation via `MultiFernet`); the audit log stores no utterance text | `test_database_holds_only_ciphertext` (SQLite **and** PostgreSQL) |
| 18 | Tampering with history | audit log is hash-chained; edits and deletions are detected; concurrent writers cannot fork the chain | `test_audit_detects_edited_entry`, `..._deleted_entry`, PostgreSQL concurrency test |
| 19 | DoS: huge bodies / passwords / queues | 64 KB body cap (also counts chunked bodies); password max 128 chars (argon2 cost); bounded caches, queues and WebSocket counts | `test_oversized_body_is_rejected`, `test_local_cache_is_bounded` |
| 20 | Credentials sent in clear | the local agent refuses plain `http://` to any non-loopback host | `test_credentials_are_refused_over_plain_http...` |
| 21 | Redis outage | Redis is an accelerator, never truth: sessions fall back to the DB, caches to a bounded local store, limits to per-process. Degradation is visible in metrics | `test_redis_outage_degrades_instead_of_failing` |
| 22 | Lost device / compromised PC | revoke the device on the dashboard: its token dies on the next request, and it cannot get a new one | `test_revoking_a_device_kills_its_live_token_immediately` |

## What is NOT done (read this)

Being clear about gaps matters more than the list above.

* **No MFA / passkeys.** Login is a password only. For a single owner on a private network this
  is reasonable; for anything internet-facing, add TOTP or WebAuthn. *Biggest remaining gap.*
* **Approvals are not step-up authenticated.** Anyone holding a live admin session can approve.
  The 15-minute token and XSS hardening limit exposure, but a re-prompt for the password on
  high-risk approvals would be stronger.
* **TLS is not terminated by this repo.** The compose stack speaks plain HTTP on 8080. Put it
  behind a TLS-terminating proxy/load balancer. The app sets `Secure` cookies and HSTS in prod.
* **Encryption keys live in process memory and the secrets directory.** This defends a stolen
  *database*, not a stolen *running server*. For stronger separation use a KMS/Vault.
* **Hash-chained audit is tamper-evident, not tamper-proof.** Someone with full DB write access
  can recompute the chain, and deleting the newest entries is undetectable. Ship logs to
  append-only storage for real assurance.
* **HS256 is a shared secret.** Fine with one backend; use asymmetric keys if other services must
  verify tokens without being able to mint them.
* **Gmail, web search, GUI automation and LLM agents are stubs.** They say so rather than
  pretending. When added they must go through the same propose → approve → execute path, and
  Google access must use OAuth with minimal scopes.
* **Not penetration-tested.** These are engineering controls with tests, not an audit. Before
  exposing this publicly, get independent review.
* **Docker files are statically checked only.** Docker was not available where this was built,
  so images and the compose stack have not been built or run end to end.
* **Voice:** the wake word and speech-to-text run locally, but a person or recording near the
  microphone can say "Hey JARVIS". Voice is a convenience, not authentication, which is why
  risky actions require a dashboard approval rather than a spoken "yes".

## Operating it safely

1. `python -m jarvis_backend init-secrets` once, then **back up `JARVIS_DATA_KEY`** somewhere
   separate from the database. Losing it makes encrypted data unrecoverable.
2. In production run `check-config`; the process also refuses to start on an unsafe config.
3. Register each PC as its own device so you can revoke one without touching the others.
4. Review **System → Audit trail** occasionally; it shows `CHAIN VERIFIED` when intact.
5. Rotate: change `JARVIS_JWT_SECRET` (keep the old one in `JARVIS_JWT_PREVIOUS_SECRETS` briefly
   to avoid logging everyone out), and prepend a new key to `JARVIS_DATA_KEY` (keep the old one
   after it so existing data stays readable).
6. If you ever see `refresh_reuse_detected` in the audit log, a refresh token was replayed.
   Treat it as a credential leak: change your password (this signs out every session).
