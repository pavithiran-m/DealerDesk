# VIKMO DealerDesk

DealerDesk is an end-to-end backend service for automotive spare parts distribution, built to strict API specifications and enterprise run contracts.

- **Track:** AI / ML Intern Track *(All required parts + all Django track optional parts completed for full bonus)*
- **LLM Provider & Model:** **Groq** (`llama-3.3-70b-versatile`), **OpenAI** (`gpt-4o-mini`), **Google Gemini** (`gemini-1.5-flash`)
- **Video Walkthrough Link:** `[Link to 5-minute video: Loom / Unlisted YouTube / Google Drive]`

---

## What Was Built

| Module | Part | Status |
|---|---|:---:|
| **Part A1** | Backend Core (Products, Inventory, Dealers, Draft Orders) | Completed (Required) |
| **Part A2** | Order Engine (Pessimistic locking, GST precision, Idempotency) | Completed (Full Bonus) |
| **Part B** | Resilient Supplier Sync (Cursor pagination, retries, deduplication) | Completed (Full Bonus) |
| **Part C** | AI Dealer Assistant (Tool calling, session memory, live grounding) | Completed (Required) |
| **Part D** | Demand Forecasting & Inventory Reorder Suggestions (Ridge ML, safety stock) | Completed (Required) |

---

## Run Contract & Instructions

```bash
# 1. Install dependencies (Linux & cross-platform ready)
pip install -r requirements.txt

# 2. Database migrations (clean initial migration included)
python manage.py migrate --noinput

# 3. Seed catalogue and dealers (strictly idempotent, run twice)
python manage.py seed_data
python manage.py seed_data

# 4. Start the server
python manage.py runserver 0.0.0.0:8000 --noreload
```

---

## Running the Smoke Test & Unit Tests

### Smoke Test
```bash
# Run against the freshly seeded database
python smoke_test.py --base-url http://localhost:8000 --all
```

### Unit & Integration Test Suite
```bash
python manage.py test
```

---

## Hosting on Render

This repository is pre-configured with a Render Blueprint (`render.yaml`), `build.sh`, and `Procfile`.

### Quick 1-Click Deployment:
1. Push this repository to GitHub.
2. In Render Dashboard (`https://dashboard.render.com/`), click **New +** > **Blueprint**.
3. Select your GitHub repository.
4. Render will automatically:
   - Provision a managed PostgreSQL database (`dealerdesk-db`).
   - Create the Web Service using `build.sh` (installs dependencies, migrates DB, seeds 600 products and 25 dealers, and runs `collectstatic`).
   - Start the app with `gunicorn dealerdesk.wsgi:application`.
5. Under Environment Variables in Render, add your `GROQ_API_KEY` (or `OPENAI_API_KEY` / `GEMINI_API_KEY`) for the AI Dealer Assistant.
6. Open your live `.onrender.com` URL!

Complete technical justifications and exact line citations are documented in **[DECISIONS.md](file:///c:/Users/pavithiran/OneDrive/Documents/VIKMO_DealerDesk_Assignment%20%281%29/VIKMO%20DealerDesk%20Assignment/starter/DECISIONS.md)**:
1. **Overselling Prevention**: Enforced via PostgreSQL row-level locks (`select_for_update()`) with products ordered deterministically by SKU to eliminate deadlocks.
2. **GST Precision**: 18% tax calculated per line using `Decimal` with `ROUND_HALF_UP` rounding to the exact paisa.
3. **Idempotency**: Handled via `Idempotency-Key` headers and payload SHA-256 hash validation.
4. **AI Grounding**: Tool calling restricts the model from hallucinating stock or prices; descriptions are treated strictly as data.
5. **Leakage-Free ML**: Multi-step lag-based Ridge regression with strict temporal holdout split.


Live Application:  (https://vikmo-dealerdesk.onrender.com/)

Complete Code-Review link:
(https://drive.google.com/file/d/1SmrEuAamd55Z786aGqpiywiItKMNHNCN/view?usp=sharing)
