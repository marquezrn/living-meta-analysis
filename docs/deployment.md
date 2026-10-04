# Render deployment and secure connections

The public code repository and the private research application are separate.
GitHub repository access does not configure application OAuth, OpenAI, Render or R2.
Use service dashboards to configure secrets; never put credentials in repository
files, reports, screenshots, issue comments or chat messages.

## Connections required

| Connection | Configure | Purpose |
| --- | --- | --- |
| Render | Connect this repository and import `render.yaml` | Application, worker, queue, PostgreSQL and metadata trigger |
| GitHub OAuth application | Client ID/secret and exact HTTPS callback | Identify the owner and invited collaborators |
| Private Cloudflare R2 bucket | Bucket, S3 endpoint, scoped access key/secret | Documents, page renders, overlays and checkpoint artifacts |
| OpenAI API project | Worker-only API key | Owner-initiated bounded extraction |
| Elsevier/institution | Key, issued institution token, verified server entitlement | Optional Scopus discovery |
| Open sources | Contact email and optional OpenAlex/NCBI keys | Polite metadata discovery and OA resolution |

## Deploy

1. In Render, create a Blueprint from `marquezrn/living-meta-analysis`. Review the
   compute plans and their current hosting prices before provisioning. Hosting is
   independent of the USD 100 OpenAI evaluation allowance.
2. Set `APP_URL` to the final HTTPS application URL on both web and worker services.
   Create a GitHub OAuth application with callback
   `https://YOUR_APP_HOST/auth/github/callback`. Configure its client ID and secret
   on the web and worker services. The owner is verified by immutable GitHub ID
   `290779630`, rather than by a mutable username alone.
3. Create an R2 bucket with public `r2.dev` access and public custom domains disabled.
   Create an object read/write credential scoped to that bucket. Configure its
   endpoint, bucket name and credentials on web and worker. The endpoint has the
   form `https://ACCOUNT_ID.r2.cloudflarestorage.com`.
4. Configure `OPENAI_API_KEY` only on the extraction worker. Keep the configured
   run and total limits at or below USD 100. The metadata cron job has no OpenAI key.
5. Configure discovery contact details and source credentials on the web and
   metadata cron services. Leave `SCOPUS_VERIFIED=false` until institutional server
   authorization is verified. Enable Scopus in a protocol only after that check.
6. Deploy. The web pre-deploy command runs Alembic migrations. Verify `/health`,
   owner sign-in, an invited reader's isolation, a private PDF view, an upload,
   and a metadata-only check before starting paid extraction.

`sync: false` secrets are entered separately for the listed services. Render's
generated common session secret is shared. The metadata cron environment uses
`ENVIRONMENT=monitor` and needs database/source configuration, not OAuth or R2 keys.
Enable Scopus verification consistently on the web and cron services after approval.

The database and queue deny public network connections in the Blueprint. R2 objects
are served through authenticated project endpoints; private bucket objects are not
made public for PDF or figure previews. A one-use invitation grants membership only
after the recipient signs in. The application does not send invitation emails.

## Weekly schedule and operational checks

Render schedules are UTC. The hourly trigger executes `livingmeta monitor-due`.
The database computes Monday 08:00 in `Europe/Madrid`, which is 06:00 UTC during
summer time and 07:00 UTC during winter time. Per-project leases and scheduled-due
checks prevent repeated processing. Partial source failures retain checkpoints and
retry after an hour; only complete metadata checks advance the freshness timestamp.

New citations remain pending. A metadata check never runs OpenAI extraction, even
when PDFs are openly available. Acquiring an authorized PDF is also separate from
paid extraction. Review source-specific status reports and the three freshness dates.

Back up PostgreSQL and preserve private R2 objects. Retain immutable run artifacts
and audit events. Resume stopped runs through the application rather than editing
the cost ledger. Queue worker concurrency is one; database reservations still guard
the shared allowance if workers are later added.

Primary setup references: [Render Blueprints](https://render.com/docs/blueprint-spec),
[Render UTC cron jobs](https://render.com/docs/cronjobs),
[GitHub OAuth authorization](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps),
[R2 authentication](https://developers.cloudflare.com/r2/api/tokens/), and
[R2 public access controls](https://developers.cloudflare.com/r2/buckets/public-buckets/).
