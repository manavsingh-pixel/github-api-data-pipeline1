# GitHub Repository Insights — Data Engineering Take-Home

## 1. What this builds
A small end-to-end pipeline that calls GitHub's free public Search API, stores the original API pages, validates and normalizes repository records into SQLite, creates a reporting table using SQL aggregation, and displays the results in Streamlit.

**API:** `GET https://api.github.com/search/repositories`  
**Default query:** `stars:>50`, sorted by stars descending  
**Storage:** SQLite  
**Transformations:** Python normalization + SQL aggregation  
**Dashboard:** Streamlit + Plotly

The dashboard describes a sampled set of repositories returned by the query. It does not claim to represent all GitHub repositories.

## 2. Architecture

```text
GitHub Search API
      |
      | paginated requests, timeout, bounded retries
      v
raw_api_responses  (raw layer: JSON payloads, status, fetch time, errors)
      |
      | validate identity, normalize nulls/counts, deduplicate by repo ID
      v
repositories       (clean layer: one repo per snapshot date)
      |
      | SQL GROUP BY language
      v
language_summary   (reporting layer: counts, stars, open issues)
      |
      v
Streamlit dashboard
```

## 3. Quick start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m src.pipeline
streamlit run src/dashboard.py
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

No API key is required for a small demo. GitHub's unauthenticated rate limit is lower; optionally set `GITHUB_TOKEN` to a personal access token with no extra scopes for public data. Never commit a token. The API query and page count can be adjusted with `GITHUB_QUERY`, `MAX_PAGES`, and `PER_PAGE` environment variables. `DB_PATH` can override the SQLite file path.

For example:

```bash
MAX_PAGES=2 PER_PAGE=100 python -m src.pipeline
```

## 4. Data model and grain

### `raw_api_responses`
**Grain:** one attempted API page per recorded request. Stores endpoint, page number, fetch timestamp, HTTP status, response JSON, and error text. Keeping the raw response helps diagnose API changes or bad records without relying on transformed data.

### `repositories`
**Grain:** one repository ID per UTC snapshot date. The composite primary key `(snapshot_date, repo_id)` allows the same repository to appear on later dates, while preventing duplicate records in one snapshot. Required identity fields are validated; missing languages become `Unknown`; malformed or negative numeric counts become zero; missing optional metadata remains null. API records are deduplicated by repository ID before loading.

### `language_summary`
**Grain:** one language per UTC snapshot date. SQL calculates repository count, total stars, average stars, and total open issues. This is reporting-ready and keeps aggregation logic out of the dashboard.

## 5. Reliability choices

- **Pagination:** requests a bounded number of pages, stopping when a page is short or empty. A cap prevents an accidental unbounded run.
- **Timeouts/retries:** 20-second request timeout, up to three attempts, exponential waits for transient errors, and respect for `Retry-After` where provided.
- **Rate limits:** if GitHub signals the rate limit is exhausted, the code checks the reset header and waits (bounded to 60 seconds per retry). A long reset may still require rerunning later.
- **Raw observability:** successful pages and failed attempts are recorded with status/error metadata.
- **Validation:** records without a numeric ID or usable full name are skipped; optional fields do not make the whole record fail.
- **Deduplication:** repo ID is the stable identifier; duplicate IDs caused by page overlap are collapsed before load.
- **Same-day reruns:** clean rows for that UTC date are replaced in a transaction, making reruns idempotent at the snapshot level. Raw attempts remain append-only.
- **Empty run protection:** if no valid records are fetched, the pipeline fails rather than deleting a prior clean snapshot.
- **Scope:** a small exercise, not production infrastructure. No scheduler, cloud warehouse, dbt, or alerting system is required by the assignment.

## 6. Metrics and how to interpret them

- **Repository count by primary language:** shows the composition of the sampled repositories. `Unknown` means the API did not provide a language.
- **Total stars by language:** a popularity signal for this sample, not a quality measure and not a causal comparison between languages.
- **Open issues:** the sum of open issue counts returned by the API. It is not a direct measure of poor quality; repositories vary greatly in size and issue-management practices.
- **Snapshot trend:** compares daily snapshots collected by repeated runs. One run cannot produce a historical trend; after the first run the dashboard explicitly says so. The sample can change as GitHub's ranking and repository metadata change.

## 7. Trade-offs and next steps

- SQLite is intentionally local and simple. PostgreSQL would be a natural next step for concurrent users and larger workloads.
- Python transformations keep the assignment approachable; dbt would be useful if the SQL model layer grew and needed model-level tests/documentation.
- This is a snapshot pipeline, not a full change-data-capture system. Same-day reruns replace the day's clean snapshot; later days retain prior snapshots.
- GitHub Search API result limits and rate limits constrain how much data is available. The pipeline is deliberately bounded.
- Production extensions could include structured metrics, alerting on stale snapshots, schema-contract tests, CI, and an orchestrator, but those are outside this time-boxed assignment.

## 8. AI usage note

AI assistance was used to scaffold the project structure, discuss data modelling and reliability cases, and draft explanatory documentation. I reviewed the design choices and kept the implementation intentionally small: SQLite instead of a cloud warehouse, Python plus SQL instead of dbt, bounded pagination/retries, a snapshot grain, and metrics that can be explained. Before submission, I would run the code and tests locally, inspect the actual API responses, and correct any behaviour that differs from the documented assumptions.

## 9. Before sharing the repository

1. Run the pipeline and dashboard locally.
2. Run the unit tests.
3. Confirm that the database file is not committed if it becomes large; add it to `.gitignore` if preferred.
4. Add a screenshot of the dashboard to the README if useful.
5. Push the project to a public or access-granted GitHub repository and share that repository URL.
