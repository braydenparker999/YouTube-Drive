# YouTube → Google Drive Archiver

A small, fully online archiver for **public videos you have permission to download**. GitHub Actions checks creator RSS feeds daily, downloads sequentially with yt-dlp, uploads to Google Drive, and checkpoints each video in an inspectable JSON file. No computer or phone needs to stay online.

**Current validation:** real YouTube downloads now pass on fresh GitHub-hosted runners using a verified Cloudflare WARP local proxy, current yt-dlp nightly, `mweb`, and bgutil PO tokens. The test checks the complete downloaded audio/video, merge, duration, and full decode. **Google Drive upload remains untested until Google authorization is supplied.** See [TESTING.md](TESTING.md) for run links and limits.

**First-use order:** prove YouTube download → authorize Google once → add four repository secrets → add creators or submit a manual URL → verify the Drive upload and duplicate skip. The initial creator list is intentionally empty. No YouTube API key, signed-in YouTube account, paid proxy, always-on personal computer, or public HTTP endpoint is required by the tested configuration.

## YouTube proof comes first

[YouTube download proof](https://github.com/braydenparker999/YouTube-Drive/actions/workflows/youtube-proof.yml) runs the same downloader and runtime installer as the archive, without Google authorization or archive-state writes. **Run workflow → main** repeats one short public video on three fresh runners, sequentially. Each must report `status: complete`, `stage: verified`, audio and video streams, matching duration, and `full_decode: true`. It saves only sanitized JSON evidence; media and raw metadata are deleted.

This gate has been exercised during the build. Rerun it after changing egress or upgrading a suspect runtime. A green unit-test workflow alone does not satisfy this gate. Configure Drive only after this proof succeeds.

## One-time setup

### 1. Create the Drive destination

In [Google Drive](https://drive.google.com/), create a **YouTube Archive** folder in your personal **My Drive**. Keep it private. Copy the ID from `https://drive.google.com/drive/folders/FOLDER_ID`; save only the ID as `GDRIVE_ROOT_FOLDER_ID` below. This v1 targets My Drive, not organizational Shared Drives.

### 2. Authorize Google entirely in your browser

Use your own OAuth client so the refresh token supports unattended operation:

1. Open [Google Cloud Console](https://console.cloud.google.com/), select/create a project, and enable **Google Drive API** under APIs & Services → Library.
2. Configure **Google Auth Platform** (or OAuth consent screen): give the app a name, supply your support/developer email, select **External**, and add your own Google account as a test user while setting it up. Complete **Branding → App domain** as well: the production Publish button can stay disabled when homepage/privacy links are missing, even if the name and email fields are filled. For this personal installation, use the [app homepage](https://missionarytube.z13.web.core.windows.net/youtube-archive/index.html), [privacy policy](https://missionarytube.z13.web.core.windows.net/youtube-archive/privacy.html), and [terms](https://missionarytube.z13.web.core.windows.net/youtube-archive/terms.html). Other operators must supply their own accurate pages. Follow Google's domain validation requirements; these links do not mean the app or domain has been verified by Google.
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

No cookies or YouTube API key are configured. The tested WARP egress is the default and needs no repository secret, paid plan, or personal Cloudflare login. `YTDLP_PROXY` optionally replaces it with your own authorized proxy. `YTDLP_EXTRACTOR_ARGS` is an optional trusted extractor override.

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
- `max_quality`: `480` by default; choices 360/480/720/1080/1440/2160.
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

MP4 is preferred when the selected codecs permit it; MKV/WebM are allowed without lossy re-encoding. The default is the best available video at or below 480p, with audio. Maximum quality is a ceiling, not a promise that YouTube exposes that resolution; lower-resolution sources are not upscaled.

Default safety/runner limits in the configuration: 25 new/retry videos per run, 4 GiB per finished file, 2 hours per video, a 210-minute budget for starting work, and at most 5 failed automatic attempts per video. After 5 failures, automatic retries pause; resolve the cause and manually resubmit the URL to retry it. One video at a time; 5–10 second download pacing; bounded extraction, fragment, upload and API retries. The workflow has a 6-hour hard limit. Runner disk is checked before each download; temporary media is removed after successful upload/checkpoint and also cleaned up when a failed job ends. Adjust settings within the validated bounds for your creators. An 8 GiB setting needs more free runner disk than the usual free hosted runner may provide because merging needs working space.

A run that reaches its budget keeps discovered channel videos in state for the next run. Manual batches first validate and persist all confirmed public entries before media downloading begins, so validated requests survive a run budget or upload failure. A manual URL that fails extraction before that point must be submitted again; it is not durably queued yet. Keep manual batches modest. Completed duplicates are global: a later request for a different destination/quality returns the original Drive record, not another copy.

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
    "max_quality": "480",
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

The tested default uses `mweb;fetch_pot=always`, IPv4, one video/fragment at a time, paced requests, and bounded retries. Each runner starts bgutil 2.0.0 from its digest-pinned image on **127.0.0.1:4416 only**. Tokens are generated automatically for the current request. Direct GitHub runner egress failed even with generated player tokens; token generation alone is not proof of YouTube access.

**Default egress is Cloudflare WARP local-proxy mode.** The runner installs the official signed package, creates an anonymous disposable device registration, binds the proxy at `127.0.0.1:40000`, and requires Cloudflare's trace endpoint to confirm `warp=on` before downloading. Registration/device credentials remain only on the disposable runner. This uses no paid WARP+ plan or organization account. Only yt-dlp and its token-provider requests are explicitly proxied. RSS discovery, GitHub state, and Google Drive use the regular network. WARP changes the network route; it cannot guarantee that YouTube will always accept every shared exit IP.

Both workflows call `scripts/setup_youtube_runtime.sh`, which explicitly installs and verifies ffmpeg/ffprobe. Downloads fail before transferring media if those tools are absent, and yt-dlp is instructed to abort on errors instead of leaving unmerged streams while reporting success.

To replace egress, set the **`YTDLP_PROXY` secret** to your authorized HTTP/SOCKS proxy URL; it takes precedence and skips WARP setup. To intentionally test bare runner egress, set the **`YTDLP_EGRESS` repository variable** to `direct` and leave the proxy secret unset. Clear that variable to restore WARP. Keep the provider and downloader on the same egress. No Google/GitHub secrets are passed into the provider container or media subprocesses. The old `YTDLP_USE_PO_PROVIDER` variable is no longer needed: the tested configuration starts the provider automatically.

The browser-based alternative was investigated but did not resolve direct-egress rejection; it is not part of the production runtime. No automatic client switching, IP rotation loop, account cookies, or random proxy list is implemented.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Missing repository secrets | Add the four exact names above. No stack trace or download occurs before setup checks pass. |
| yt-dlp extraction / JS failure | Inspect logged version; verify Deno and EJS install steps. Try one known public video. If a nightly regression is identified, set `YTDLP_VERSION` to the last working version. |
| YouTube HTTP 403 / sign-in / IP challenge | Check the egress health and yt-dlp/provider versions. Run the separate proof once. WARP/shared IPs may be blocked later; leave bounded retries enabled, or test an authorized replacement via `YTDLP_PROXY`. Do not add account cookies by default. |
| WARP setup/tunnel failure | The runtime stops before YouTube downloads. Retry a later run; inspect official-package installation and the egress health step. A custom proxy secret replaces WARP without redesigning the pipeline. |
| Missing ffmpeg/ffprobe | The shared runtime installation must pass before downloading; do not remove the explicit apt install/preflight. |
| YouTube HTTP 429 | Stop immediate reruns. Keep pacing/low concurrency; retry on the next scheduled run or reduce the batch size. |
| Google `invalid_grant` / expired authentication | Reauthorize using your own OAuth client in production and replace the refresh-token secret. Testing-mode tokens normally expire after 7 days. |
| Drive 403 / quota | Check account storage, Drive API enabled, granted scope and write permission to the root. `storageQuotaExceeded` is not solved by repeated retries. |
| Drive upload failed | Successful videos stay complete. The same local file is retried within the job. Later runs verify the reserved ID before redownloading. Restore any trashed destination/file; do not reset state. |
| Duplicate request | Expect `skipped: true` and the existing Drive ID, even if the new destination or quality differs. No second copy is created. |
| State HTTP 403 / conflict | Check Actions write permission and branch rules. Stop concurrent/manual state edits; rerun after resolving the conflict. Never bypass this by deleting state. |
| Creator feed fails | Verify a stable UC ID; feed outages are isolated. Pending discovered videos still process. No full-channel scrape occurs. |
| Scheduled run missing | Inspect Actions, default-branch workflow, disabled-workflow banner and GitHub status. Schedules can be delayed; manually dispatch with blank URLs for catch-up. |
| File/duration/disk limit | Lower quality or carefully adjust settings. Live/upcoming/post-live entries wait until a final VOD is exposed. After the configured failed-attempt limit, automatic retries pause; resubmit a URL manually after fixing the cause. |
| New manual URL failed before metadata | Resubmit it. Public metadata confirmation occurs before its durable entry is created. |

## Development and tests

```bash
python -m pip install -r requirements.txt ruff==0.15.0
python -m unittest discover -s tests -v
python -m ruff check src scripts tests
```

Unit tests use simulated Drive/GitHub failures and tiny local bytes, never credentials or real media downloads. Coverage includes URL/feed parsing, validation, filenames, state updates/conflicts, duplicate skips, failed-creator/video isolation, upload/checkpoint crash recovery, chunk offset recovery, expired sessions, OAuth refresh, secret/log isolation and workflow security.

The **Tests** workflow runs without secrets or YouTube requests on PRs and trusted pushes. The separate **YouTube download proof** workflow is manual-only, restricted to the default branch, and uses no Google credentials. It tests the actual downloaded media, rather than stopping at metadata. A passing unit job does not imply a passing network proof.

The archiver itself runs only on schedule/manual dispatch from the default branch; there is no `pull_request_target` or secret-bearing PR workflow. Third-party Actions are pinned to commit SHAs, checkout does not persist Git credentials, workflow inputs enter environment variables rather than shell programs, and yt-dlp/JS/ffmpeg subprocesses do not receive Google/GitHub credentials.

## Upstream references

- [yt-dlp YouTube extraction guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors)
- [yt-dlp EJS/runtime setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [yt-dlp PO-token guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [Drive resumable uploads and pre-generated IDs](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
- [Google OAuth refresh-token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Google personal-use verification exception](https://support.google.com/cloud/answer/13464323)
- [bgutil PO-token provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
- [GitHub concurrency queues](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

- [Cloudflare WARP Linux setup](https://developers.cloudflare.com/warp-client/get-started/linux/)
- [Cloudflare WARP local proxy mode](https://developers.cloudflare.com/warp-client/warp-modes/)
