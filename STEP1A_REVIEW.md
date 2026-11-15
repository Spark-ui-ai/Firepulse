# FirePulse Step 1A — hardened review candidate

This branch is a review candidate only. It is not merged into `main` and is not deployed to Render.

## Files

- `server_3.py`: explicit route handling, strict input validation, fixed upstream error messages, shared four-worker upstream pool, two-request proxy admission limit, bounded HTTP handler threads, per-chunk response-size limit, and total upstream read deadline.
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

The Python tests mock all NASA FIRMS traffic; they do not require a NASA API key or make upstream requests.

## Known limitation — API key transport

Step 1A retains the existing URL-path API key contract so it remains compatible with the original dashboard flow. The server redacts keys from its own request-line logs and never echoes upstream bodies, but a key in a URL can still appear in browser history, developer tools, proxy/access logs, or monitoring systems. Migrate the key to a request header in a separate Step 1B before treating this as production-hardened.

## Review status

The frontend date-batching logic was exercised in the assistant's JavaScript runtime across five date ranges; all batches were contiguous, non-overlapping, and at most ten days, and the UTC source checks passed.

The Python test suite has been corrected to target `server_3.py`, but still needs to be run locally using the commands above. Do not merge or deploy based only on source inspection.
