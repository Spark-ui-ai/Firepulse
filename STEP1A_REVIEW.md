# FirePulse Step 1A + Step 1B — hardened review candidate

This branch is a review candidate only. It is not merged into `main` and is not deployed to Render.

## Files

- `server_3.py`: explicit route handling, strict input validation, header-only MAP_KEY authentication, fixed upstream error messages, shared four-worker upstream pool, two-request proxy admission limit, bounded HTTP handler threads, per-chunk response-size limit, and total upstream read deadline.
- `firepulse.html`: patched dashboard used by `server_3.py`.
- `firepulse_3.html`: standalone snapshot of the patched dashboard.
- `firepulse.html.patch`: original frontend patch for review.
- `test_server.py`: regression tests importing `server_3` and reading the patched `firepulse.html`.
- `fe_dates_test.js`: frontend date-batching checks against the patched `firepulse.html`.

## Local verification

Run these commands from the repository root:

```bash
python -m py_compile server_3.py test_server.py
python -m unittest -v test_server
node fe_dates_test.js
```

Start the candidate locally with:

```bash
python server_3.py
```

Then open http://localhost:8080/.

The Python tests mock all NASA FIRMS traffic; they do not require a NASA API key or make upstream requests. The Render Blueprint starts `server_3.py`, matching the hardened proxy and the updated frontend.

## API key transport — Step 1B

The browser sends the MAP_KEY in the `X-FIRMS-MAP-KEY` request header. The key is no longer part of the FirePulse proxy URL, so it is not placed in browser history or request-line logs. The server rejects the legacy key-in-path format. The key remains visible to the user in browser developer tools and to the browser itself; this change improves transport/logging hygiene but is not a substitute for HTTPS or a server-side secret store.

## Review status

The frontend date-batching logic was exercised in the assistant's JavaScript runtime across five date ranges; all batches were contiguous, non-overlapping, and at most ten days, and the UTC source checks passed.

**CI verified:** GitHub Actions passed on commit `64b276513e40ea483a02e483317568518f84c413`: Python compilation, all 40 Python tests, and the frontend date tests. The Render start-command correction was subsequently committed as `459978c88c249a6e8072cdc723bd68a07735fff0`; its CI run should be checked before merging. The suite mocks NASA FIRMS; it does not replace one real fetch with the user's own key.
