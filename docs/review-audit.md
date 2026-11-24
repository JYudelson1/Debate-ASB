# Human audit viewer

Build a portable offline page from an existing end-to-end run:

```sh
uv run python scripts/build_review_audit.py artifacts/end-to-end/closure-luna-20261028
```

Open `review-audit.html` in that run directory. No server, network, or new model calls are required. The page is a snapshot; rebuild it to include later reviews.

**Fix verdicts** starts with the model's yes/partial repair claims. Change **Reviewer says** to No to inspect possible false negatives, or All fix verdicts to browse everything. One task is one proposed fix, identified by codebase, extraction epoch, bundle, debate repetition, and fix rank. **Evidence coverage** audits the additional model's target-coverage labels; any prior analyst judgment is displayed separately.

Use **A** to agree, **1–5** to supply a repair correction (**1–6** for coverage), **U** for follow-up, **S** to skip, **J/K** for next/previous, **Z** to undo, and **/** to search. Typing in a field disables these shortcuts. The bottom decision bar stays visible. Across codebases alternates among codebases using a deterministic browsing order; it is not a random statistical sample.

**Prompt inspector** contains actual logged requests selected deterministically across codebases/extraction epochs, coverage labels, fix verdicts, prompt hashes, and validation-feedback retries. The system text and user packet hashes must match the saved review, and the visible model output must match its fix judgments and target explanation. The inspector provides readable packet sections and downloadable exact strings. It excludes private model reasoning. Logged source windows are used as recorded; current files are never substituted for missing historical requests.

Decisions and notes save in browser storage, separately from the experiment. **Export audit** creates a JSON backup; **Import** merges matching entries and preserves existing entries. Stored entries are bound to hashes of the bundle and original model review, including its prompt/input hashes. Entries from changed reviews are not silently reapplied. Storage availability for local files varies by browser; the page warns if saving fails. Keep an export when stopping or changing browsers. **My audit** collects disagreements, follow-ups, skips, and notes.

The builder checks bundle, registry reference, logged input, and fix identities. Existing analyst labels are never overwritten, and an unmatched logged request is reported rather than reconstructed. No fix is applied, no model is invoked, and no scores or source review files are changed. Builder, viewer, report, study, prompt, packet, and matched-log hashes are retained for provenance.

Checks:

```sh
uv run pytest tests/test_review_audit.py -q
node scripts/check_review_audit.cjs artifacts/end-to-end/closure-luna-20261028/review-audit.html
```
