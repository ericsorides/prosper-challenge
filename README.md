# Prosper Challenge

This is a template repository for an AI voice agent that is able to schedule appointments for a health clinic. To do that the agent connects in real-time to the clinic's CRM system, which in the healthcare industry is known as an Electronic Health Record (EHR). The foundations are already set:

- Pipecat is configured with sensible defaults and the bot already introduces itself when initialized
- Playwright is set up so that you can programmatically log into Healthie, the EHR we'll use for this challenge

This fork implements the missing pieces:

- **Conversation** — Collects full name and date of birth, calls **`find_patient`**, then asks for **visit type** (initial 60 min vs follow-up 45 min), **date**, and **time**, and calls **`create_appointment`** with those fields.
- **`healthie.find_patient`** / **`healthie.create_appointment`** — Playwright automation against the Healthie web app (see **`SOLUTION.md`** for behavior and trade-offs).

## Setup

To get started, fork this repository so that you can start commiting and pushing changes to your own copy.

### Prerequisites

#### Environment

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager installed

#### Healthie Account

You'll need a Healthie account for testing, you can create one [here](https://secure.gethealthie.com/users/sign_up/provider).

### Installation

1. Clone this repository

   ```bash
   git clone <repository-url>
   cd prosper-challenge
   ```

2. Copy the API keys we've shared with you, as well as your Healthie credentials:

   Create a `.env` file:

   ```bash
   cp env.example .env
   ```

   Then, add your API keys and credentials:

   ```ini
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   OPENAI_API_KEY=your_openai_api_key
   HEALTHIE_EMAIL=your_healthie_email
   HEALTHIE_PASSWORD=your_healthie_password
   # Optional: run Chromium headless (e.g. servers)
   # HEALTHIE_HEADLESS=true
   ```

3. Set up a virtual environment and install dependencies

   ```bash
   uv sync
   ```

4. Install Playwright browsers

   ```bash
   uv run playwright install chromium
   ```

### Running the Bot

```bash
uv run bot.py
```

**Open http://localhost:7860 in your browser** and click `Connect` to start talking to your bot.

> 💡 First run note: The initial startup may take ~20 seconds as Pipecat downloads required models and imports.



## Expectations & Deliverables

To make the agent functional we expect you to implement at least the following missing functionalities:

1. **Conversation flow** — Implemented in `bot.py` (system prompt + tools); see [Pipecat function calling](https://docs.pipecat.ai/guides/learn/function-calling).

2. **`healthie.find_patient(name, date_of_birth)`** — Implemented in `healthie.py`.

3. **`healthie.create_appointment(patient_id, date, time, appointment_type=...)`** — Implemented in `healthie.py`; the voice tool passes **`appointment_type`** (`initial_consultation` or `follow_up`) to match Healthie’s modal.

4. **Integration** — Pipecat handlers in `bot.py` call the Healthie helpers during the call.

We encourage you to use AI tools (Claude Code, Cursor, etc.) to help you with this challenge. We don't mind if you "vibe code" everything, that probably means you have good prompting skills. What we do care about is whether you understand the decisions and trade-offs behind your solution. That's why, apart from the code itself, we'd like you to write a high-level overview of your solution and the decisions you've made to get to it—do this in a `SOLUTION.md` file at the root of your fork. **This repository includes `SOLUTION.md` for that overview.** During the interview we'll dive deeper into it and discuss opportunities to improve it in the future.

If you'd like to go further, you can already document some of those potential improvements in your `SOLUTION.md`. Some areas that we'd love to hear your thoughts on are:
- Latency: balancing speed with user experience and accuracy
- Reliability: ensuring that the agent is always available to answer, regardless of external factors (e.g. AI provider unavailable)
- Evaluation: making it easy for us to check that the agent is behaving how it is supposed to


Once you are done, please share the link to your fork so that we can get familiar with it before our chat.
