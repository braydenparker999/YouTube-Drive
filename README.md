# YouTube → Google Drive Archiver

A small, fully online archiver for **public videos you have permission to download**. GitHub Actions checks creator RSS feeds daily, downloads sequentially with yt-dlp, uploads to Google Drive, and checkpoints each video in an inspectable JSON file. No computer or phone needs to stay online.

**First-use checklist:** authorize Google once → add four repository secrets → add creators or submit a manual URL → run the Action. The initial creator list is intentionally empty. There is no YouTube API key, service account, server, dashboard, or public HTTP endpoint.

## One-time setup

### 1. Create the Drive destination

In [Google Drive](https://drive.google.com/), create a **YouTube Archive** folder in your personal **My Drive**. Keep it private. Copy the ID from `https://drive.google.com/drive/folders/FOLDER_ID`; save only the ID as `GDRIVE_ROOT_FOLDER_ID` below. This v1 targets My Drive, not organizational Shared Drives.

### 2. Authorize Google entirely in your browser

Use your own OAuth client so the refresh token supports unattended operation:

1. Open [Google Cloud Console](https://console.cloud.google.com/), select/create a project, and enable **Google Drive API** under APIs & Services → Library.
2. Configure **Google Auth Platform** (or OAuth consent screen): give the app a name, supply your support/developer email, select **External**, and add your own Google account as a test user while setting it up.
3. Under **Data Access**, add `https://www.googleapis.com/auth/drive`. This scope can access your Drive, not just this folder. The implementation confines destinations to your configured root; the broad scope allows a browser-only setup with an existing folder without building a Google Picker application. Use this client only for your own account/project.
4. Under **Audience**, change publishing status to **In production** **before** issuing the final token. An External app left in Testing normally receives Drive refresh tokens that expire after **7 days**. Personal-use apps can qualify for Google's verification exception; an unverified-app warning may still appear. Follow your account/organization's policy if it blocks consent.
5. Under **Clients**, create an OAuth client of type **Web application**. Add exactly `https://developers.google.com/oauthplayground` as an authorized redirect URI. Copy its **Client ID** and **Client secret**.
6. Open [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/). Click the gear/settings icon, check **Use your own OAuth credentials**, enter your Client ID and Client secret, select **Access type: Offline** and **Force prompt: Consent** if shown. Do not use the Playground's default client.
7. In Step 1, enter `https://www.googleapis.com/auth/drive`, click **Authorize APIs**, choose the Google account owning the archive folder, and approve your own app. Check the selected account carefully.
8. In Step 2, click **Exchange authorization code for tokens**. Copy the **Refresh token** into the GitHub secret below. An access token is short-lived and is **not** the value to save. Do not click Revoke tokens afterward. Do not paste tokens in chat, issues, source files, or screenshots.

The runner automatically obtains fresh access tokens from the saved refresh token. Reauthorization is only necessary if Google revokes/expires it, you revoke consent, or your app/account policy changes. If you previously authorized in Testing, issue a new token after switching to production.

### 3. Add these four GitHub repository secrets

Open [Settings → Secrets and variables → Actions](https://github.com/braydenparker999/YouTube-Drive/settings/secrets/actions) → **New repository secret**:

| Secret | Value |
| --- | --- |
| `GDRIVE_CLIENT_ID` | Your Google OAuth Web application Client ID |
| `GDRIVE_CLIENT_SECRET` | The matching OAuth Client secret |
| `GDRIVE_REFRESH_TOKEN` | Refresh token from the authorization above |
| `GDRIVE_ROOT_FOLDER_ID` | ID of your private YouTube Archive folder |

`GITHUB_TOKEN` is supplied automatically by GitHub. **Do not create a personal GitHub token for daily operation.** The archive job requests `contents: write` so it can checkpoint `state/archive.json`. Repository/organization policy and branch rules must permit that write; a rule requiring PRs for every `main` change will block checkpoints. Failures stop before uploads if state cannot be saved.

No proxy, cookies, PO token, or YouTube API key is required by the initial configuration. Optional `YTDLP_PROXY` and `YTDLP_EXTRACTOR_ARGS` secrets are discussed under troubleshooting, not prerequisites.

### 4. Configure creators

Edit only [`config/channels.yml`](config/channels.yml). Replace `channels: []` with entries like this, substituting actual stable IDs:

```yaml
channels:
  - name: Creator One
    channel_id: UC_REPLACE_WITH_REAL_CHANNEL_ID
    drive_folder: Creator One
    enabled: true
  - name: Creator Two
    channel_id: UC_REPLACE_WITH_ANOTHER_CHANNEL_ID
    drive_folder: Creator Two
    enabled: true
```

A valid channel ID is `UC` followed by 22 letters/numbers/underscores/hyphens. You can find your own in YouTube advanced account settings; for another creator, inspect the channel page's canonical/channel-ID metadata. Handles and arbitrary channel URLs are deliberately not resolved during daily runs. Placeholder IDs are rejected when enabled.

`drive_folder_id` may optionally identify a preexisting creator folder directly under the archive root. Otherwise the app finds an existing uniquely named folder once, or creates it, then remembers its ID. Folder names allow letters, numbers, spaces, `.()-_` (80 characters max); slashes and `..` are rejected. Set `enabled: false` or remove a creator to stop its outstanding channel jobs. Completed records remain. Pending records keep their original destination and quality; changing configuration applies to newly discovered records.

**First run queues approximately the latest 15 uploads per enabled creator.** This is not a full historical backup. RSS usually exposes a rolling window of about 15 entries. If more than that are published between successful discoveries, omitted videos cannot be recovered from RSS; submit their URLs manually. RSS failure is reported and retried next day; v1 does not silently switch to channel scraping or require an API key.

### 5. Run a manual end-to-end test

Open [Actions → YouTube → Drive archive](https://github.com/braydenparker999/YouTube-Drive/actions/workflows/youtube-archive.yml) → **Run workflow**, select **main**, and enter one short public video you are authorized to download:

- `urls`: one URL, multiple URLs separated by whitespace/newlines, or a JSON array of URL strings.
- `destination`: `Requested` by default; one folder name under the root.
- `max_quality`: `1080` by default; choices 360/480/720/1080/1440/2160.
- `request_id`: optional correlation label, e.g. `jarvis-1234` (no personal/private content).

Leave `urls` blank to check configured creators and retry eligible pending work. A URL with playlist parameters archives just that video's canonical ID. Playlist-only URLs, non-YouTube hosts, HTTP URLs, path traversal, and malformed values are rejected. Maximum 25 manual URLs per invocation.

Confirm a file appears in `YouTube Archive/Requested`, the JSON record becomes `complete`, and the run summary contains its `drive_file_id`. **Run the same URL again:** it must report `skipped: true` with the same ID and perform no extraction/download/upload for that video.

## Daily operation

The schedule is **08:23 UTC / 05:23 Buenos Aires daily**, off the busiest top-of-hour interval. GitHub schedules are best-effort: starts can be delayed or missed. Scheduled workflows run only from the default branch. GitHub may disable public-repository schedules after 60 days of repository inactivity; state commits provide activity while work is being archived, but check/re-enable Actions after a long idle period. This is a background appliance with explicit failure reporting, not a guarantee that every datacenter IP or schedule invocation will work forever.

GitHub-hosted standard public-repository jobs can run without minute charges, subject to GitHub's usage policies, concurrency and runtime limits. This is not an unlimited VM or guaranteed download service. Drive storage counts against your Google storage quota. Large media never enters Git or GitHub artifacts.

Scheduled layout:

- `YouTube Archive/Creator One/2026/YYYY-MM-DD - Video Title [VIDEO_ID].mp4`
- `YouTube Archive/Creator Two/2026/...`
- `YouTube Archive/Requested/...` for manual requests (no year subfolder).

MP4 is preferred when the selected codecs permit it; MKV/WebM are allowed without lossy re-encoding. Maximum quality is a ceiling, not a promise that YouTube exposes that resolution.

Default safety/runner limits in the configuration: 25 new/retry videos per run, 4 GiB per finished file, 2 hours per video, and a 210-minute budget for starting work. One video at a time; 5–10 second download pacing; bounded extraction, fragment, upload and API retries. The workflow has a 6-hour hard limit. Runner disk is checked before each download; temporary media is removed after successful upload/checkpoint and also cleaned up when a failed job ends. Adjust settings within the validated bounds for your creators. An 8 GiB setting needs more free runner disk than the usual free hosted runner may provide because merging needs working space.

A run that reaches its budget keeps discovered channel videos in state for the next run. Manual videos are persisted once metadata confirms they are public. A manual URL that fails extraction before that point must be submitted again; it is not durably queued yet. Keep manual batches modest. Completed duplicates are global: a later request for a different destination/quality returns the original Drive record, not another copy.

## Durable state and recovery

[`state/archive.json`](state/archive.json) is the source of truth, keyed by YouTube video ID. It records channel, title, publication date, source/destination, quality, status, attempts, Drive ID, local-media size/checksum, completion time and credential-free errors. Folder IDs are cached alongside it. Metadata and history are **public**; only request public videos and non-sensitive destination/request names. Drive media itself remains private. This is not suitable for private/unlisted videos or private request histories.

1. Discover RSS entries and checkpoint the entire new window.
2. Skip globally completed IDs before contacting YouTube.
3. Check any already reserved Drive ID; if its uploaded size, MD5 and YouTube ID match, recover completion without downloading.
4. Extract metadata, reject unfinished live videos/non-public content/oversized durations, then download and validate a video stream with ffprobe.
5. Checkpoint the size/checksum and reserve a **pre-generated Drive file ID in GitHub before uploading**.
6. Upload in 8 MiB resumable chunks. On uncertain chunk responses, probe the server offset and continue with the local file. Expired sessions restart with the same Drive file ID; an already-created ID cannot produce a second file.
7. Independently fetch Drive metadata and compare YouTube ID, byte size and MD5. Only then checkpoint `complete`, and delete local media.

A killed runner loses its disk and upload session URI. If the upload had completed, the next run verifies the reserved ID and skips downloading. Otherwise it may need to download again and start a new resumable session; it still uses the same reserved ID. Session URLs, signed media URLs and credentials are never stored in Git or artifacts. This is deliberate: resume within a run, safe restart across disposable runners.

The whole archive workflow uses one concurrency group, `cancel-in-progress: false`, and `queue: max` (up to 100 pending runs). State is fetched fresh from GitHub after the run starts, not trusted from a stale checkout. Content-SHA compare-and-swap detects outside/concurrent edits. If a state write times out, the app reads back to see whether it committed. It stops on unresolved conflicts instead of continuing with unsafe state. Normal video/creator failures do not stop other work.

**Do not delete/reset archive state or edit it while an archive is running.** Its reserved IDs close the upload/checkpoint race. Force-push rollback, moving/deleting Drive files, deleting state, or running independent copies outside the workflow's concurrency guard can invalidate these guarantees. A trashed/mismatched reserved file produces a clear failure; restore/repair it instead of deleting its record. Completed records are intentionally not reverified daily, so external deletions are not automatically repaired.

## Logs and future Jarvis integration

Logs contain JSON-escaped `[DISCOVERY]`, `[DOWNLOAD]`, `[UPLOAD]`, `[SUMMARY]` entries; titles cannot inject Actions commands. Raw exceptions/HTTP bodies, signed URLs and yt-dlp metadata are suppressed. Final `run-summary.json` is saved as a 14-day artifact named `archive-summary-RUN_ID-ATTEMPT`, and successful/failed/skipped/deferred counts appear in the Actions summary. A partially failed run exits nonzero after preserving successes. Setup errors exit 2 with the names of missing secrets.

Jarvis can later POST to GitHub's authenticated endpoint:

`POST /repos/braydenparker999/YouTube-Drive/actions/workflows/youtube-archive.yml/dispatches`

```json
{
  "ref": "main",
  "inputs": {
    "urls": "[\"https://www.youtube.com/watch?v=BaW_jenozKc\"]",
    "destination": "Requested",
    "max_quality": "1080",
    "request_id": "jarvis-1234"
  }
}
```

GitHub workflow inputs are strings: serialize Jarvis's URL array into the `urls` string (or join with newlines). A future GitHub App/fine-grained token needs Actions write to dispatch and Actions read to locate the run/download its summary. That credential belongs in Jarvis's secret storage, never this public repository or frontend. The request ID appears in the run title and summary; correlate it with the run ID and attempt. It is a correlation label, not an independent deduplication key. Video IDs are the deduplication keys. No Jarvis UI, endpoint or API token has been added here.

```json
{
  "request_id": "jarvis-1234",
  "status": "complete",
  "videos": [
    {"video_id": "BaW_jenozKc", "status": "complete", "drive_file_id": "example-file-id", "skipped": true}
  ],
  "discovery_errors": [],
  "successful": 0,
  "skipped": 1,
  "failed": 0,
  "deferred": 0
}
```

Top-level status: `complete`, `partial`, `setup_required`, `fatal`, or `interrupted`. A hard runner termination can prevent a final artifact; GitHub's run status and durable state remain authoritative in that case. Partial artifacts checkpoint each processed video, so they may not enumerate untouched items after a hard termination.

## yt-dlp / networking decisions

Each run installs one current prerelease/nightly via `pip install --upgrade --pre 'yt-dlp[default]'`, including the matching EJS challenge scripts. The version is logged and never updated mid-batch. Deno 2.9.6 is installed as the JS runtime, and ffmpeg/ffprobe handle merges/validation. Set the **repository variable** `YTDLP_VERSION` to a previously tested PyPI version (for example the exact version printed in installation logs) to roll back; clear it to resume nightly updates.

The default uses yt-dlp's current default YouTube clients, no account cookies, IPv4, one fragment at a time and conservative retry/pacing settings. Upstream recommends trying `mweb` with a PO-token provider when default clients fail. This is **not blindly enabled**: a working provider must be explicitly installed/configured first. One optional trusted `YTDLP_EXTRACTOR_ARGS` secret feeds yt-dlp's extractor configuration so that a provider/client change stays isolated in `src/downloader.py`. Do not put credentials in config/channels.yml. No plugin/server is installed unnecessarily in v1.

An optional `YTDLP_PROXY` repository secret is passed only to yt-dlp, not to Drive or state/discovery. It can contain an authenticated egress URL. Do not rotate random clients, cookies, proxies and tokens together. Diagnose first. Proxy/PO support is an extension point, not a claim that every GitHub/Azure runner IP works. A single failed test is reported rather than followed by aggressive repeated probes.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Missing repository secrets | Add the four exact names above. No stack trace or download occurs before setup checks pass. |
| yt-dlp extraction / JS failure | Inspect logged version; verify Deno and EJS install steps. Try one known public video. If a nightly regression is identified, set `YTDLP_VERSION` to the last working version. |
| YouTube HTTP 403 / sign-in / IP challenge | Public videos can be rejected from datacenter IPs. Try a later run once. If repeatable, configure and test a documented PO-token provider with `mweb`, or authorized alternate egress. Do not add account cookies by default. |
| YouTube HTTP 429 | Stop immediate reruns. Keep pacing/low concurrency; retry on the next scheduled run or reduce the batch size. |
| Google `invalid_grant` / expired authentication | Reauthorize using your own OAuth client in production and replace the refresh-token secret. Testing-mode tokens normally expire after 7 days. |
| Drive 403 / quota | Check account storage, Drive API enabled, granted scope and write permission to the root. `storageQuotaExceeded` is not solved by repeated retries. |
| Drive upload failed | Successful videos stay complete. The same local file is retried within the job. Later runs verify the reserved ID before redownloading. Restore any trashed destination/file; do not reset state. |
| Duplicate request | Expect `skipped: true` and the existing Drive ID, even if the new destination or quality differs. No second copy is created. |
| State HTTP 403 / conflict | Check Actions write permission and branch rules. Stop concurrent/manual state edits; rerun after resolving the conflict. Never bypass this by deleting state. |
| Creator feed fails | Verify a stable UC ID; feed outages are isolated. Pending discovered videos still process. No full-channel scrape occurs. |
| Scheduled run missing | Inspect Actions, default-branch workflow, disabled-workflow banner and GitHub status. Schedules can be delayed; manually dispatch with blank URLs for catch-up. |
| File/duration/disk limit | Lower quality or carefully adjust settings. Live/upcoming/post-live entries wait until a final VOD is exposed. Permanent unavailable videos remain failed until configuration/state is deliberately reviewed. |
| New manual URL failed before metadata | Resubmit it. Public metadata confirmation occurs before its durable entry is created. |

## Development and tests

```bash
python -m pip install -r requirements.txt ruff==0.15.0
python -m unittest discover -s tests -v
python -m ruff check src scripts tests
```

Unit tests use simulated Drive/GitHub failures and tiny local bytes, never credentials or real media downloads. Coverage includes URL/feed parsing, validation, filenames, state updates/conflicts, duplicate skips, failed-creator/video isolation, upload/checkpoint crash recovery, chunk offset recovery, expired sessions, OAuth refresh, secret/log isolation and workflow security.

The **Tests** workflow runs without archive secrets on PRs and trusted pushes. Its separate metadata smoke job makes one bounded request to yt-dlp's public test video on trusted pushes/manual test runs only; it downloads no media and intentionally reports a failure when the runner cannot extract. A green unit job does not imply that a red YouTube smoke job is healthy. The archiver itself runs only on schedule/manual dispatch from the default branch; there is no `pull_request_target` or secret-bearing PR workflow. Third-party Actions are pinned to commit SHAs, checkout does not persist Git credentials, workflow inputs enter environment variables rather than shell programs, and yt-dlp/JS/ffmpeg subprocesses do not receive Google/GitHub credentials.

## Upstream references

- [yt-dlp YouTube extraction guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors)
- [yt-dlp EJS/runtime setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [yt-dlp PO-token guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [Drive resumable uploads and pre-generated IDs](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
- [Google OAuth refresh-token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Google personal-use verification exception](https://support.google.com/cloud/answer/13464323)
- [GitHub concurrency queues](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
