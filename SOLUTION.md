# Solution overview

This fork completes the [Prosper challenge](https://github.com/Prosper-Technologies/prosper-challenge): a voice agent collects caller details, looks up the patient in **Healthie** (EHR), and books an appointment via **Playwright** browser automation.

## Deliverable explanation and changes

SSo, to sum up, this deliverable fulfills everything required in the README (see the detailed description of the functions at the end of this document). However, several changes have occurred since the challenge was originally created. I will outline the main differences here.

I assume that, since the challenge was written, the Healthie web application has significantly changed, which required me to adapt most of the provided functions. The login process is now done in two steps (enter email, click, then enter password). The patients page—and everything depending on it—has been removed and replaced with a clients page. This is not just a naming change; the underlying data structure has also been modified. Additionally, appointment creation now requires an extra field indicating whether it is a first visit or a follow-up, which had to be integrated into both the function and the conversational flow.

A significant portion of the development time was spent addressing these changes, which impacted the overall elegance of the code due to time constraints.

I would also like to point out that, due to slow connection issues, many timeouts were added to the code to prevent errors and allow sufficient time for page hydration. The VAD parameters were initially too permissive, causing frequent interruptions, so some of the Silero VAD parameters were adjusted. To partially mitigate latency from the user’s perspective, I accessed the web application before starting the conversation, effectively hiding that delay from the user. There are, of course, more robust ways to handle this, depending on how much is known about the upcoming interaction.

Another aspect I did not have time to properly address—but which should not be too difficult—is implementing a clean and reliable way to gracefully end the call.

## Potential Improvements

Below is a list of improvements that should be considered if this approach were to be developed into a production-ready system.

### Latency

First, based on my limited experience with this solution, latency is largely dominated by web scraping. The latency introduced by AI models is relatively negligible in comparison. Ideally, we would rely on an existing API—or build one—for the Healthie platform to significantly improve performance. If that is not feasible, an alternative would be to maintain an external dataset that periodically mirrors Healthie’s data (while meeting all necessary cybersecurity requirements). The system would interact in real time with this dataset and synchronize changes back to Healthie at intervals. Although this could introduce potential conflicts (which would require analysis of how frequently manual updates occur in Healthie), it would greatly improve responsiveness.

Second, improvements could be made to the VAD system. Silero VAD is somewhat outdated, and newer approaches go beyond simple silence detection by incorporating tone and contextual cues to better determine when a user has finished speaking. For example, tone-based models (such as those explored by organizations like Kyutai) or lightweight language models could be used.

Third, several techniques could be applied to improve LLM performance, such as quantization and speculative decoding. While these optimizations are not always accessible when using third-party APIs, some may still be worth exploring.

Finally, perceived latency can be reduced through user experience design. For example, playing predefined messages such as “We are checking the database for your information” while backend processing occurs can mask delays and improve the overall experience.

### Reliability

To prevent a single AI provider outage from disrupting the entire system, a fallback strategy should be implemented. This could involve maintaining a secondary model (e.g., switching between providers) for each component (LLM, TTS, STT). A comprehensive testing suite would be essential to benchmark and select the best-performing options. Running a lightweight local model could also be considered, although it may be less practical.

The dataset approach mentioned earlier would also help mitigate issues related to Healthie platform downtime.

Finally, a robust error-handling system is necessary to ensure failures are managed gracefully and properly logged for future debugging and improvement.

### Evaluation

As mentioned above, a comprehensive testing framework should be developed for both individual components and the full pipeline. This could range from simple unit-test-like checks (e.g., verifying that an appointment is successfully created) to more complex scenario-based evaluations.

For production evaluation, one straightforward approach is to collect user feedback at the end of each interaction (e.g., through a short questionnaire). Using standard A/B testing methods, different system configurations could be compared based on user ratings. However, this approach only evaluates features already deployed in production.

To test new features, simulated interactions could be generated using multiple LLMs (one acting as the client, another as the assistant), with a third model evaluating the interaction, potentially guided by human annotations. This remains a complex and open problem that would benefit from further exploration and structured experimentation.

## Architecture

- **`bot.py`** — Pipecat pipeline (ElevenLabs STT/TTS, OpenAI LLM), `FunctionSchema` tools, handlers that call `healthie` and return structured JSON for the model.
- **`healthie.py`** — Reused browser session (`login_to_healthie`), Playwright-only automation against `secure.gethealthie.com`.

# DETAILS

## `find_patient`

1. Navigate to **Clients**, wait for the full-page loader (`#loading-state-container`) to disappear.
2. Locate the list search field as **`[role="main"] form[role="search"] input`** (Healthie’s clients toolbar)
3. Set search text with **`_react_set_search_value`** (click, fill, dispatch `input`/`change`)
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
