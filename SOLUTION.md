# Solution overview

This fork completes the [Prosper challenge](https://github.com/Prosper-Technologies/prosper-challenge): a voice agent collects caller details, looks up the patient in **Healthie** (EHR), and books an appointment via **Playwright** browser automation.

## Deliverables vs README

| README expectation | Implementation |
|-------------------|----------------|
| Conversation: name, DOB, then date & time | **Plus** visit type: `initial_consultation` (60 min) or `follow_up` (45 min), required by Healthie’s booking modal. |
| `find_patient(name, date_of_birth)` | `healthie.find_patient` — clients list search, profile navigation, DOB verification with format variants. |
| `create_appointment(patient_id, date, time)` | Extended to **`create_appointment(patient_id, date, time, appointment_type=None)`**; the voice tool always passes `appointment_type`. |
| Pipecat integration | `bot.py` registers tools, system prompt enforces flow, **pre-login** on connect to reduce first-tool latency. |

## Architecture

- **`bot.py`** — Pipecat pipeline (ElevenLabs STT/TTS, OpenAI LLM), `FunctionSchema` tools, handlers that call `healthie` and return structured JSON for the model.
- **`healthie.py`** — Reused browser session (`login_to_healthie`), Playwright-only automation against `secure.gethealthie.com`.

### Voice UX (turn-taking)

Interim transcripts were starting a new “user turn” too early and **interrupting** the LLM mid–tool-call. The bot uses **`UserTurnStrategies`** with **VAD-only start** (`VADUserTurnStartStrategy`) and **`TurnAnalyzerUserTurnStopStrategy`** with slightly longer **`stop_secs`** on Silero VAD so the model can finish `create_appointment` before the next turn.

## `find_patient`

1. Navigate to **Clients**, wait for the full-page loader (`#loading-state-container`) to disappear.
2. Locate the list search field (ordered selectors scoped to `main` / role=main first).
3. Set search text with **`_react_set_search_value`** (click, fill, dispatch `input`/`change`) so React’s debounced search runs.
4. Prefer **not** pressing Enter when rows / “Active clients” already show a match — Enter was breaking SPA state (including treating `/appointments/new` as the literal client name `new`).
5. Collect profile link candidates from `/users/{id}` and `/clients/{id}` patterns and table context.
6. Open candidate profiles, normalize body text, match DOB with multiple textual formats (and a cautious **sole exact-name** fallback when DOB text is absent).

## `create_appointment`

1. Open **`/clients/{patient_id}`**, wait for the same global loader as on the list page.
2. Open **Book session** from `data-testid="book-session-with-{id}"` or visible **Book session** text (polled).
3. Scope the modal with **`[data-testid="modal-content"]`** (avoids picking the wrong `role=dialog`, e.g. chat widgets).
4. **Appointment type** — combobox labeled “Appointment type”; choose **Initial Consultation - 60 Minutes** or **Follow-up Session - 45 Minutes** from the listbox. This step is **required**; booking fails if skipped.
5. **Date / time** — `input#date` / `input#time`, dismiss react-datepicker poppers with **Escape**, use **`force`** on time when needed; broken `aria-labelledby` on the time field makes role+name matching unreliable, so IDs are primary.
6. **Submit** — scroll modal content to the bottom, then **`[data-testid="primaryButton"]` (“Add appointment”)** via Playwright **`force` clicks**, in-page **DOM** activation (pointer + mouse + `click()` + `form.requestSubmit(btn)`), and a **second click after ~480 ms** because the first interaction sometimes commits time formatting before the real submit.

Success is inferred from URL (`/appointments/...`) and body text heuristics (not a dedicated API response).

## Trade-offs

| Choice | Why | Cost |
|--------|-----|------|
| Playwright, not private API | Matches the starter stack; no credential reverse-engineering. | Brittle to UI/CSS changes; slower than API. |
| Heuristic success / DOB | Practical without backend hooks. | False positives/negatives possible on unusual tenants. |
| Blocking tool handlers | Simple mental model for the LLM. | Long pauses on slow Healthie loads; user hears silence unless TTS fills the gap. |
| Single shared browser page | Faster repeat tool calls after first login. | Session expiry or navigation bugs affect all calls until re-login. |

## Future work (latency, reliability, evaluation)

- **Latency** — Long-lived context + tab pool; stream a short “one moment” TTS filler while tools run; parallel pre-fetch of client page when DOB is confirmed.
- **Reliability** — Classify failures (auth, selector, validation, network); bounded retries; optional headless flag is already **`HEALTHIE_HEADLESS=true`** for servers.
- **Evaluation** — Recorded Playwright traces for golden paths; contract tests on handler JSON; scripted transcripts (found / not found / slot conflict / double-booking).

## Running locally

See **README.md**: `cp env.example .env`, `uv sync`, `uv run playwright install chromium`, `uv run bot.py`, then open the printed local URL and connect.
