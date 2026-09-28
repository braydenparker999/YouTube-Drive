# Validation record — 2026-09-28

## Outcome

The implementation is committed and its local functional/recovery/security tests pass. **A real YouTube → Drive archive is not yet proven operational.** Google OAuth/root secrets have not been supplied to this build, and tested GitHub-hosted YouTube metadata extraction was rejected by YouTube's sign-in/IP challenge.

Do not interpret a passing unit-test workflow as a successful YouTube network test or Drive upload.

## Tests performed

| Check | Result |
| --- | --- |
| URL extraction, host allowlist, JSON/newline input parsing, duplicate URLs | Passed |
| Atom parsing, channel ownership, malformed XML, latest-15 window | Passed |
| Filename sanitation and Unicode byte limits | Passed |
| Complete-record skip, same Drive ID returned on duplicate request | Passed |
| Upload succeeds but completion checkpoint is lost | Passed; recovered without another download/upload |
| Drive ID reservation checkpoint fails | Passed; no upload attempted |
| Interrupted chunk probes server offset and continues | Passed with simulated HTTP responses |
| Expired upload session restarts with the same file ID | Passed with simulated HTTP responses |
| Final-response conflict recovers the existing Drive file | Passed with simulated HTTP responses |
| OAuth refresh and safe expired-credential errors | Passed with simulated token endpoint |
| Drive byte size, MD5 and YouTube-ID verification | Passed with simulated Drive metadata |
| Concurrent state writer and uncertain GitHub checkpoint response | Passed with simulated GitHub API |
| Large state uses immutable blob lookup above Contents API's inline limit | Passed |
| One failed creator/video does not abort later work | Passed |
| Manual batches persist before media downloading | Passed |
| Failed extraction increments attempts; exhausted automatic retries pause | Passed |
| Workflow parsing, schedule, manual input declarations, concurrency | Passed |
| No shell expression interpolation, no secret-bearing PR jobs, no pull_request_target | Passed |
| Google/GitHub secrets omitted from downloader child environment | Passed |
| Log newline/Actions-command injection and credential-shaped source scan | Passed |
| Real yt-dlp download + ffprobe check against tiny locally generated MP4 | Passed; downloaded SHA-256 matches source and metadata file is removed |
| Missing Google secrets | Passed: exit 2, clear SETUP message, no stack trace |
| GitHub-hosted test workflow | Started successfully; unit, lint and missing-secret startup jobs passed |
| Archive workflow registration | GitHub API reports active, ID 369471602 |
| Real Google OAuth / Drive upload | Not run: authorization secrets are missing |
| Real scheduled/manual archive job to completion | Not run: external prerequisites remain |

The local suite contains **42 tests**: 41 safe/unit tests plus the opt-in real local-media integration test. Enable the integration test with `YTDRIVE_INTEGRATION=1` after installing yt-dlp and ffmpeg. Regular CI intentionally skips that optional test if its flag is absent.

## Hosted network evidence

Only bounded metadata requests were made; no YouTube video media was downloaded.

1. [Initial CI](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36474002561): unit/startup tests passed; the old `BaW_jenozKc` fixture failed. A separate diagnostic confirmed that video was unavailable. Replaced that fixture.
2. [Ubuntu/default clients](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36474493251): current nightly `2026.09.27.232945`, Deno/EJS installed, public `jNQXAC9IVRw` metadata failed with a sign-in/IP challenge. Unit/startup tests passed.
3. [Ubuntu/mweb + PO provider](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36474784191): bgutil 2.0.0 installed, local provider health check passed, same public video's metadata still received a sign-in/IP challenge. No cookies/proxy were added. Unit/startup tests passed.
4. [macOS/default clients](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36475006139): a standard macOS runner, using its different hosting network, also received the sign-in/IP challenge. Unit/startup tests passed.

The workspace's first local YouTube check additionally encountered its proxy's CA trust issue. A diagnostic using the configured system trust store confirmed the old fixture was unavailable; TLS verification was not disabled. The successful local-media integration test does not depend on YouTube access.

After diagnosing the external block, network smoke testing was made **explicitly opt-in** (`Tests` workflow → `network_smoke=true`) so routine code pushes do not repeatedly probe a blocked service. `po_provider=true` selects the documented `mweb` combination for that diagnostic. The final archiver retains default clients, with an optional provider repository variable and a secret-based egress extension point.

## Remaining prerequisites

1. Complete the README's browser-only Google authorization and create `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_REFRESH_TOKEN`, and `GDRIVE_ROOT_FOLDER_ID` repository secrets.
2. Establish a working permitted YouTube egress route from the archiver runner and test it. `YTDLP_PROXY` is supported for an authorized proxy if needed; no external proxy was purchased, provisioned, or assumed to work. A PO token alone did not fix the observed block. A different future runner might work, but that has not been demonstrated here.
3. Add the actual creator channel IDs to `config/channels.yml` (currently empty), or supply one authorized public video URL for the first manual test.

First end-to-end acceptance: one short authorized public video uploads, Drive size/MD5/YouTube ID verify, state becomes complete, and rerunning the URL returns exactly the same Drive ID with `skipped: true`. Repeat using an enabled creator for scheduled-mode discovery, then confirm the next daily invocation. Those real acceptance checks must happen after the external prerequisites are ready.
