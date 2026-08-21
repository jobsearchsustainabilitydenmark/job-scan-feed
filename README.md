# Daily Denmark job scanner — prototype v1

A free, low-compute pipeline for discovering newly seen jobs without email:

- **LinkedIn** via [JobSpy](https://github.com/speedyapply/JobSpy)
- **Jobindex** via Jobindex's public search page and its embedded `Stash.searchResponse` data
- **GitHub Actions** runs the scan on GitHub-hosted infrastructure
- `data/seen_jobs.json` makes "new since last scan" deterministic
- `data/latest.json` is the machine-readable hand-off to ChatGPT
- `data/history/YYYY-MM-DD.json` keeps an audit trail

## Why the overlap works

LinkedIn is queried for the last **30 hours**. Jobindex is queried for the last **3 days** (`jobage=3`, newest first), then `seen_jobs.json` removes anything already observed. The larger Jobindex overlap protects against delayed indexing while still reporting only jobs not seen in previous runs.

## Positive control

`config.json` currently contains LinkedIn job ID **4456487107** (Atea Head of Sustainability) as a positive control. Every run records whether that ID was found. This is for testing discovery coverage; remove it once the pipeline is validated.

## First-run behavior

The first run has no history, so **every job returned by the overlapping search windows is considered newly seen**. From run 2 onward, `seen_jobs.json` makes the feed truly "new since last scan".

## Source health

`latest.json` explicitly records each source as `ok`, `partial`, `failed`, or `disabled`. If LinkedIn is blocked or Jobindex changes its HTML, the feed shows the failure instead of falsely reporting "no jobs".

## Search tuning

Edit `config.json` to change search terms, location, time window, overlap and result counts. The current terms are deliberately broad for the prototype.

## Notes / limitations

- JobSpy uses public job-board endpoints/scraping rather than an official LinkedIn Jobs API, so LinkedIn can change or rate-limit access.
- Jobindex's public search page currently embeds structured result data; the scanner parses that data rather than opening every job advert.
- The scanner performs **discovery**, not final suitability assessment. ChatGPT should score and optionally verify only the strongest new matches.
