# Verification record - November 11, 2026

Story: a clinician searches a natural-language phrase, FastAPI ranks actual local document/summary embeddings, and the UI opens the matching patient's source-linked summary and FHIR records.

## Automated checks

- **41 passed** with `RUN_EMBEDDING_TESTS=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 pytest` on Python 3.12.14.
- Ordinary `pytest` skips the one model-dependent integration test and requires no model download or API key.
- 55 synthetic input rows -> 50 accepted documents, 1 duplicate removed, 4 quarantined rows, 10 patients, 100 FHIR resources, 90 indexed document/report/summary entries.
- Pydantic intermediate validation, fhir.resources Python validation, exact HL7 R4 4.0.1 JSON Schema and reference checks passed.
- Independent HL7 validator 7.0.5: **10 bundles, 0 errors, 0 warnings, 80 informational notices**. Remote terminology server disabled; MIME-type terminology checks are therefore unverified. See `fhir-validation-report.json`.
- Actual OpenAI SDK request/response parsing tested with an HTTP mock; missing key, refusal and upstream error paths are explicit. No real model API call was performed.
- Real-model integration queries recovered the expected patient in the top three for iron deficiency, diabetes and reduced renal filtration. This is a smoke evaluation, not a clinical retrieval benchmark.
- Compiled Tailwind stylesheet and `node --check web/app.js` passed.

## Performance

See `benchmark.json`: 25 warm FastAPI TestClient POST requests over 50 documents; median **25.92 ms**, p95 **35.04 ms**, maximum **42.69 ms**. Includes query embedding, vector ranking, filtering and API serialization; excludes cold loading, download, remote transport and browser rendering. Interactive browser requests also completed below the two-second target during observed checks. No concurrent-load guarantee is claimed.

## Browser checks

| Flow | Observed evidence |
| --- | --- |
| Initial load | 50 documents, 10 patients, 100 FHIR resources; recent records rendered |
| Semantic query | Five ranked anemia matches for “low blood count and depleted iron stores” |
| Suggestions | Respiratory, glucose and kidney shortcuts populated the query and returned results |
| Type filters | Diagnostic-report and patient-summary requests changed the displayed resource types |
| Date filters | A future date returned the empty state; reversed range showed an explanatory error |
| Reset | Cleared the query and filters and restored recent documents |
| Patient drawer | Correct patient demographics, four sections, word count, source links and disclaimer |
| Keyboard | Enter opened a result; Escape closed the native dialog; focus returned to the result |
| Data quality | Modal displayed 55 input, 50 accepted, 4 quarantined, 1 duplicate and 0 schema/reference errors |
| Mobile | 390 x 844 layout and drawer inspected; document width 390 px, no horizontal overflow |
| Loading | Skeletons and loading announcement observed during requests |
| Console | No browser console errors in the final build |

Desktop and mobile screenshots are included. No overlap, horizontal clipping or broken controls were observed in the inspected states. Long result lists and resource lists intentionally scroll. The browser was left on the local preview for review.

## Verification limits

No live OpenAI key was configured, so the actual model's selection quality, latency and cost remain unverified. The application implements the live provider and never labels demo excerpts as AI generation. No clinician review, assistive-technology audit, external deployment, Epic integration, production security review or remote terminology validation was performed. GitHub CI status is separate from the local results above.
