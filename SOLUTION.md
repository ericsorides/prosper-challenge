# Solution Overview

I implemented the missing pieces required to make the scheduling bot functional:

1. Added conversation flow instructions so the assistant:
   - asks for patient full name and date of birth,
   - finds the patient in Healthie,
   - asks for appointment date and time,
   - creates the appointment and confirms the result.
2. Implemented `find_patient(name, date_of_birth)` in `healthie.py`.
3. Implemented `create_appointment(patient_id, date, time)` in `healthie.py`.
4. Integrated these functions into Pipecat function calling in `bot.py`.

## What I Changed

### `bot.py`

- Added Pipecat function-calling tool schemas using `FunctionSchema` and `ToolsSchema`.
- Registered two function handlers on the LLM service:
  - `find_patient`
  - `create_appointment`
- Updated system instruction to enforce the required scheduling flow.
- Updated first greeting prompt to request name and date of birth at conversation start.
- Added robust handler responses so the LLM can handle:
  - missing fields,
  - patient-not-found,
  - appointment-create failures.

### `healthie.py`

- Implemented `find_patient`:
  - logs in to Healthie via existing `login_to_healthie`,
  - navigates to patients list,
  - searches by name,
  - opens candidate patient pages,
  - validates date of birth from page content when possible,
  - returns patient payload with `patient_id`.
- Implemented `create_appointment`:
  - opens patient page,
  - tries common "new appointment" entry points,
  - fills date/time fields with selector fallbacks,
  - submits form and validates success from URL/content,
  - returns appointment payload including `appointment_id` when available.
- Added helper utilities:
  - `_normalize_date` to convert common date formats to `YYYY-MM-DD`,
  - `_first_visible_locator` to improve resilience across UI variants.

## Design Decisions and Trade-offs

- **UI automation approach**: I used Playwright browser automation because it matches the starter repository setup and does not require reverse engineering private APIs.
- **Selector fallback strategy**: Healthie UI selectors can change; I used multiple selector candidates to reduce brittleness.
- **Best-effort verification**: Patient DOB matching and appointment success validation are implemented using page text/URL checks. This is practical but not as reliable as a formal API response.
- **Synchronous function calls**: Tool handlers currently wait for Healthie operations to finish before responding, which keeps dialog state simpler.

## Known Limitations

- Healthie tenant-specific UI differences may require additional selectors.
- Date of birth verification depends on profile text visibility and may fail in some layouts.
- Appointment success checks are heuristic (URL/body content) rather than explicit backend confirmation.

## Future Improvements

- **Latency**
  - Reuse a persistent authenticated browser context across calls and pre-load patients pages.
  - Add explicit progress messaging while tool calls are running.
- **Reliability**
  - Add retries with backoff for transient navigation and element timeouts.
  - Add alternate transport/provider fallbacks (STT/TTS/LLM) when one provider is down.
  - Add structured error categories so the LLM can recover predictably.
- **Evaluation**
  - Add an automated test harness with mocked Playwright pages for tool handlers.
  - Add transcript-based scenario tests for:
    - happy path,
    - patient not found,
    - invalid date/time,
    - slot unavailable.
  - Track metrics for function-call success rate and total booking completion rate.
