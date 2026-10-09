# Validation record — 2026-09-28

## Outcome

**The YouTube download gate passed on three fresh GitHub-hosted Ubuntu runners, followed by a fourth successful run through the final shared installer.** Each downloaded the complete public test video's audio and video through Cloudflare WARP, merged them, checked duration/streams, and decoded the entire file with ffmpeg. No signed-in YouTube account, account cookies, paid proxy, or Google Drive authorization was used.

**The complete YouTube → Drive archive is not yet end-to-end verified:** Google OAuth/root secrets are still missing. Unit and simulated recovery tests are separate evidence from the real download proof.

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
| Real scheduled/manual archive job to completion | Not run: Google authorization remains |
| Real YouTube media on three fresh runners | Passed: download, A/V merge, duration and full ffmpeg decode |
| Missing ffmpeg and misleading traceback status codes | Regression tests pass; fail clearly before download |
| Shared proof/archive runtime and secret isolation | Workflow/security tests pass |

The local suite contains **47 tests**: 46 safe/unit tests plus the opt-in real local-media integration test. Enable the integration test with `YTDRIVE_INTEGRATION=1` after installing yt-dlp and ffmpeg. Regular CI intentionally skips that optional test if its flag is absent.

## Hosted network evidence

### Successful acceptance run

[Run 36484792964](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36484792964), commit `c9b77be74fa312fb488a827fde16f0db544f0dda`, performed three sequential jobs on fresh runners:

| Trial | Job | Result | Media |
| --- | --- | --- | --- |
| 1 | [109138966780](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36484792964/job/109138966780) | Complete, fully decoded | 474,599 bytes; 19.028 seconds; video + audio |
| 2 | [109138966769](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36484792964/job/109138966769) | Complete, fully decoded | 474,599 bytes; 19.028 seconds; video + audio |
| 3 | [109138966721](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36484792964/job/109138966721) | Complete, fully decoded | 474,599 bytes; 19.028 seconds; video + audio |

Fixture: `jNQXAC9IVRw` (Me at the zoo), 320×240 source. Runtime: yt-dlp nightly `2026.09.27.232945`, Deno 2.9.6, matching EJS, ffmpeg/ffprobe 6.1.1, bgutil 2.0.0, WARP client 2026.7.1377.0. `mweb;fetch_pot=always`, IPv4, one fragment at a time, paced requests. Both yt-dlp and the token provider used the same loopback WARP proxy; its active tunnel was verified before contacting YouTube.

After consolidating setup into `scripts/setup_youtube_runtime.sh`, [run 36485713460, job 109141991910](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36485713460/job/109141991910) independently passed the same full download/decode check on another fresh runner. This directly exercised the installer now used by both final workflows. Its safe unit/lint/startup job also passed. The one-time push-triggered acceptance job was removed afterward; the retained network proof is manual-only.

The proof records each file's SHA-256, duration, byte size and streams. Container hashes can differ because muxing metadata changes between runs. No media, tokens, signed media URLs, browser profiles, device keys, or raw yt-dlp metadata were published as artifacts or committed. The test does not write completion records to archive state.

This proves small public-video downloads on fresh runners with the tested route. It does **not** prove uninterrupted future YouTube access, every creator/region, high-resolution or multi-hour stress behavior, or Drive upload. WARP exit addresses can be blocked later; bounded failures and the replaceable proxy interface remain necessary.

### Diagnostic history and fixes

- [Ubuntu/default clients](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36474493251), [Ubuntu/mweb provider](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36474784191), and [macOS/default clients](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36475006139) failed with the sign-in/IP challenge.
- [Explicit bgutil player token](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36482575230) was generated successfully, but YouTube returned `LOGIN_REQUIRED` and the bot check. A generated token alone was not sufficient.
- Browser-provider investigation found unsupported forced source binding, a Chromium sandbox incompatibility, and nodriver's too-short cold-start deadline. After using installed Google Chrome with its sandbox and extending the pinned startup wait, [the browser provider minted a player token](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36484002157), but direct egress still received `LOGIN_REQUIRED`. Experimental browser machinery was removed from production; its diagnostic commits preserve the investigation.
- [First WARP comparison](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36483766832) reached playable formats. [Output inspection](https://github.com/braydenparker999/YouTube-Drive/actions/runs/36484375076) confirmed both video and audio had downloaded, but the proof job had omitted ffmpeg. Its piped version check had hidden the missing command. Fixed by explicit installation/preflight and `--abort-on-error`; only merged, verified files count as success.
- Added credential-free allowlisted diagnostics. Fixed a classifier that mistook traceback line 407 for HTTP 407. Added a regression test for failure-summary logging.
- Extended the local real-media test to cover separate A/V formats, metadata serialization/reload, merging and full decode. Local synthetic media is not counted as YouTube access evidence.

The final proof is manual-only and uses the same runtime installer and downloader as the archive. Routine code pushes and PR tests do not hit YouTube. Google credentials are absent from the proof workflow. No browser sandbox or TLS verification was disabled.

## Remaining prerequisites

1. With the YouTube download gate now demonstrated, complete the README's browser-only Google authorization and create `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_REFRESH_TOKEN`, and `GDRIVE_ROOT_FOLDER_ID` repository secrets.
2. Add actual creator channel IDs to `config/channels.yml` (currently empty), or supply one authorized public video URL for the first manual test.

No additional YouTube account or proxy credential is required for the tested default. The remaining external blocker for the first real Drive test is Google authorization/root configuration.

First end-to-end acceptance: one short authorized public video uploads, Drive size/MD5/YouTube ID verify, state becomes complete, and rerunning the URL returns exactly the same Drive ID with `skipped: true`. Repeat using an enabled creator for scheduled-mode discovery, then confirm the next daily invocation. Those real acceptance checks must happen after the external prerequisites are ready.

## Readiness and offline protocol fixtures

`tests/test_readiness.py` checks exact missing/invalid fields, secret redaction,
read-only operation, folder ownership/location, root-state conflicts, sanitized
OAuth failures and explicit upload-date provenance. `tests/test_archive_fixture.py`
exercises the real main pipeline, GitHub checkpoint protocol and Drive upload
protocol against deterministic in-memory endpoints and synthetic media bytes.
It covers manual → duplicate → daily reuse, daily → manual reuse, and recovery
after an upload succeeds but the completion checkpoint fails. Assertions verify
durable ID reservations precede creation/upload and retries make no second
transfer. These are offline engineering checks; they do not replace the
authorized Google upload/duplicate acceptance described above.

New state records include `youtube_upload_date` only when a valid yt-dlp
`upload_date` exists, with `youtube_date_source: "yt-dlp.upload_date"`. RSS
publication times and extraction timestamps never supply that field. This
allows a metadata-only My Media planner to use explicit provenance without
treating historical `published_at` values as proof of an original upload date.
