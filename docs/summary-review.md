# Summary-quality validation

The implementation prioritizes attributable evidence over fluent paraphrase. `SummaryDraft` requires four clinical sections, each containing exact source-linked excerpts. Missing findings remain absent. `Summary` adds the provider, model, prompt version, confidence description, disclaimer and total word count.

## Automated checks

- Validate the strict structured-output response with Pydantic.
- Reject citations outside the current patient's document set.
- Reject non-verbatim or incomplete-sentence excerpts, including changed values and removed negation fragments.
- Require fewer than 200 words including disclaimer/confidence text.
- Test content/model/provider/prompt-aware cache behavior and reject invalid cached contents.
- Exercise the real OpenAI SDK with a mocked HTTP transport; test refusal, missing credentials and upstream errors.
- Display demo mode distinctly from live AI output; never automatically substitute demo content for a provider failure.

## Manual rubric for a real API run

After configuring a key, run `python -m ehr_media.cli ingest --summary-provider openai` and review all ten patients in the UI. For each section, compare every excerpt to the linked source. Record pass/fail for:

1. **Attribution:** every excerpt belongs to this patient and its displayed source.
2. **Fidelity:** negation, uncertainty, units, laterality and numeric values are preserved.
3. **Coverage:** chief concern, documented diagnoses, recent imaging/labs and explicitly flagged anomalies are represented when available.
4. **Temporal context:** recent findings are distinguished from historical findings; conflicts are not silently resolved.
5. **Restraint:** no new diagnosis, clinical inference, recommendation, or invented assurance of normality.
6. **Length and labeling:** under 200 words, visible AI disclaimer, no invented numerical confidence.

Include adversarial notes that contain instructions, contradictory values and absent diagnoses. A prompt plus source matching is not a complete prompt-injection defense: incorrect but verbatim evidence selection is still possible. A clinician must assess utility and safety before real deployment.

## Recorded status

Initial development used deterministic demo summaries because no live API key was configured. Automated source/length/cache/adapter checks and UI source tracing were run. Live model quality, clinician agreement and real-world performance remain unverified; do not present the demo results as an LLM evaluation.
