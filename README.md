# dubizzle Car Assistant Prototype

A local conversational AI prototype for browsing used-car inventory, answering listing-specific questions, remembering buyer preferences across sessions, validating viewing requests, and capturing qualified leads.

The application uses a **Streamlit** chat UI, a **FastAPI** backend, **LiteLLM** tool calling, **SQLite** for inventory and memory, and a hybrid preprocessing pipeline that combines conservative deterministic extraction with optional LLM gap-filling.

## Key Capabilities

- Multi-turn conversational inventory search
- Short-term session memory and persistent user-profile memory
- Viewing-slot validation against business hours
- Retry handling and configurable LLM fallback

## Architecture

```text
Streamlit UI
    |
    | POST /chat
    v
FastAPI
    |
    v
Agent / LiteLLM
    |
    +----------------+----------------+----------------+
    |                |                |                |
    v                v                v                v
Inventory        Car details      Viewing tool     Lead / memory
SQLite           SQLite           validation       SQLite + CSV
    ^
    |
Processed cars.csv
    ^
    |
Hybrid preprocessing
(deterministic extraction -> LLM fills missing fields only)
```

The frontend deliberately sends **one backend request per user turn**. Retries and model fallback happen inside the LLM layer rather than by replaying the entire `/chat` request, which reduces the risk of repeating side effects such as lead creation.

---

## Evaluator Setup Instructions

The project is packaged with `pyproject.toml`. The commands below are for Anaconda PowerShell prompt, which is the environment used during development.

### 1. Clone the repository

```
git clone https://github.com/muhammadnajjar14/dubizzle_submission.git
cd dubizzle_submission
```

### 2. Create and activate the environment

```
pip install uv
uv venv
.venv\Scripts\activate (Or follow activation message on screen- it differed when I tried)
uv pip install -e .

I faced a problem with the above once. If needed, try:
uv pip install -r pyproject.toml
```

### 3. Configure the Gemini API key

```
$env:GEMINI_API_KEY="YOUR_API_KEY"
```

The runtime models can also be overridden without editing the code:

```powershell
$env:DUBIZZLE_LLM_MODEL="provider/model-name"
$env:DUBIZZLE_LLM_FALLBACK="provider/fallback-model-name"
```

The preprocessing pipeline supports separate optional overrides:

```powershell
$env:ENRICHMENT_MODEL="provider/model-name"
$env:ENRICHMENT_FALLBACK_MODEL="provider/fallback-model-name"
```

### 4. Prepare the inventory

`data/cars.csv` is already present, this step can be skipped. I have mentioned them for diagnostic purposes.

To rebuild the processed inventory using deterministic extraction plus LLM gap-filling:

```
python scripts\preprocess.py --restart
```

For a quick deterministic-only diagnostic run:

```
python scripts\preprocess.py --restart --limit 100 --no-llm
```

Useful preprocessing flags:

- `--restart` removes the existing `cars.csv` and rebuilds it
- `--limit N` processes only the first `N` listings
- `--no-llm` disables LLM enrichment and uses deterministic extraction only
- `--sleep N` controls the delay between enrichment calls
- without `--restart`, already processed listing IDs are skipped so a run can resume from the existing CSV

### 5. Initialize SQLite databases

```
python app\retrieval.py
python app\memory.py
```

This creates/refreshes:

- `data/inventory.db` for car inventory
- `data/state.db` for session history and user-profile memory

### 6. Launch the backend

```
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Optional health check:

```
Invoke-RestMethod http://127.0.0.1:8000/health
```

### 7. Launch the Streamlit frontend

Open a second PowerShell window:

```
cd dubizzle_submission
.\.venv\Scripts\Activate
$env:GEMINI_API_KEY="YOUR_API_KEY"
streamlit run streamlit_app.py
```

---

## Design and Implementation

### 1. Streamlit frontend

Streamlit keeps the prototype lightweight while still providing a useful conversational demo. The UI maintains a generated `session_id`, a configurable `user_id`, and the visible chat history in `st.session_state`.

The sidebar includes controls for:

- switching users
- starting a new session for the same user

Each user turn results in a single POST request to the FastAPI backend with a connect/read timeout. The frontend does **not** replay the complete request on model-provider errors because a replay could duplicate tool side effects.

### 2. FastAPI backend

The backend exposes:

- `POST /chat` — sends a user turn through the agent
- `GET /health` — simple local health check

FastAPI keeps the transport layer separate from the agent, memory, retrieval, and business tools so those components can be tested independently and reused by another client if needed.

### 3. Agent and tool calling

The agent converts explicit user constraints into structured retrieval filters. Search results are intentionally compact. Full descriptions are not returned for every match until specificed. This keeps model context smaller and avoids repeatedly passing large dealer descriptions into the LLM.

### 4. Reliability and control flow

There are two different bounded mechanisms:

**Provider retries / fallback**

- LiteLLM retries individual provider calls up to three times.
- A configured fallback model is attempted only for retryable provider failures.
  
**Agent tool loop**

- A conversation turn is limited to a maximum of three tool-calling rounds.
- This prevents an uncontrolled model/tool loop while still allowing search -> details -> response style workflows.

Tool and model calls also include lightweight timing/logging so failures and slow calls are easier to inspect locally.

---

## Inventory Preprocessing

The original dataset contains fields such as listing ID, year, make, model, trim, title, description, and photo URL, while useful search attributes are often embedded inside unstructured dealer descriptions.

`scripts/preprocess.py` enriches the dataset with:

- `price_aed`
- `mileage_km`
- `body_type`
- `transmission`
- `fuel_type`

### Deterministic extraction first

The deterministic layer is intentionally conservative and prioritizes precision over coverage.

It includes:

- Arabic digit normalization
- limited Arabic support for values such as price and odometer wording
- contextual price extraction that prefers asking/cash/selling prices
- rejection of monthly payments such as `/mo`, `per month`, `P.M.`, installments, down payments, salary requirements, registration fees, and other non-vehicle amounts
- contextual mileage extraction that rejects top speed, `km/h`, service intervals, warranty mileage, battery range, and EV driving range
- support for valid low/zero-mileage new vehicles

If a value is not reliably supported by the listing, it remains `NULL`.

### LLM gap-filling second

The LLM enrichment stage runs only for fields that deterministic extraction could not resolve.

Important safeguards:

- deterministic values are never overwritten by the LLM
- the model is instructed not to guess unsupported numeric values
- monthly financing, service mileage, warranty mileage, EV range, and top speed are explicitly excluded

This hybrid approach keeps the searchable inventory useful while preserving `NULL` for genuinely missing or ambiguous attributes.

---

## Inventory Retrieval

The processed inventory is loaded into SQLite because the primary search constraints are structured and auditable.

`search_inventory` supports:

- make
- model
- minimum and maximum year
- minimum and maximum price
- maximum mileage
- body type
- transmission
- fuel type
- free-text keyword

Keyword search uses SQL `LIKE` matching over the title, description, and trim. Numeric filters operate directly on structured columns, so a listing with an unknown price does not incorrectly satisfy a numeric price constraint.

Searches return up to five compact results. When a price constraint is present, known prices are ordered first by ascending price; when a mileage constraint is present, known mileage is ordered first by ascending mileage. Otherwise, results are ordered by year and listing ID.

`get_car_details` retrieves the full row for a specific listing only when requested. Long descriptions are capped before being returned to model context.

### Why not a vector database?

For this prototype, most important constraints are exact or numeric rather than semantic. SQLite provides deterministic filtering, is easy to inspect, requires no external infrastructure, and is sufficient for the dataset size. Semantic/vector search is therefore treated as a possible future enhancement rather than a requirement for the current scope.

---

## Memory and Lead Qualification

Two forms of memory are stored in `data/state.db`:

**Short-term session memory**

- conversation messages are stored by `session_id`
- the most recent messages are loaded back in chronological order for the next agent turn

**Long-term user-profile memory**

- useful buyer preferences such as budget and preferred vehicle type are stored by `user_id`
- the same profile can be reused after starting a new session

The `save_qualified_lead` agent tool separates preference memory from lead creation:

- budget / preferred vehicle type can be retained even when no contact information has been supplied
- a row is written to `data/leads.csv` only when the agent has both a name and contact information

---

## Viewing Requests

Viewing rules are enforced deterministically in Python rather than relying on the model:

- Monday through Saturday only
- 8:00 AM inclusive to 8:00 PM exclusive
  
The current implementation **validates a viewing request but does not persist a real appointment**. Persistent booking storage or integration with an external calendar/scheduling platform is outside the current prototype scope.

---

## Testing

Run the full test suite with the project interpreter:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

The test suite covers three areas.

### Preprocessing tests

Regression cases cover common extraction failures observed in the supplied inventory, including:

- cash price vs. monthly financing
- salary and processing fees vs. vehicle price
- monthly-payment text after a total price
- top speed vs. odometer mileage
- explicit and `done ... km` mileage
- automatic accessories vs. automatic transmission
- explicit automatic transmission

### Retrieval tests

Retrieval is tested against an isolated temporary SQLite database for:

- make search
- price ranges
- correct handling of `NULL` prices
- body type
- combined filters
- electric fuel type
- keyword search
- compact result shape
- detailed listing retrieval
- unknown listing IDs
- no-match behavior

### Tool tests

Business-rule tests cover:

- Sunday rejection
- before-hours rejection
- after-hours rejection
- valid Saturday viewing
- invalid datetime input
- qualified-lead CSV persistence

---

## Project Structure

```text
dubizzle_submission/
├── app/
│   ├── agent.py              # Agent loop, tool definitions, retries/fallback
│   ├── main.py               # FastAPI backend
│   ├── memory.py             # Session history and persistent user profile
│   ├── retrieval.py          # SQLite inventory search and listing details
│   └── tools.py              # Viewing validation and lead persistence
├── scripts/
│   └── preprocess.py         # Hybrid dataset cleaning/enrichment pipeline
├── tests/
│   ├── test_preprocessing.py
│   ├── test_retrieval.py
│   └── test_tools.py
├── data/
│   ├── Copy_of_sample_cars_dataset.xlsx
│   ├── cars.csv              # Generated processed inventory
│   ├── inventory.db          # Generated SQLite inventory
│   ├── state.db              # Generated memory database
│   └── leads.csv             # Created when qualified leads are saved
├── streamlit_app.py          # Streamlit chat client
├── pyproject.toml
└── README.md
```
---

## Scope and Known Limitations

This implementation is intentionally a local prototype rather than a production marketplace service.

Within scope:

- conversational inventory discovery
- structured and keyword search
- multi-turn session context
- cross-session preference memory
- listing detail retrieval
- viewing-slot validation
- qualified lead capture
- robust provider retry/fallback behavior
- reproducible inventory enrichment
  
Outside the current scope:

- real booking persistence or calendar integration
- authentication / authorization
- production-grade PII handling and secrets management
- distributed/concurrent database architecture
- semantic/vector retrieval and embeddings
- advanced ranking/recommendation models
- full observability/tracing infrastructure
- production frontend hardening

For local convenience, FastAPI currently allows broad CORS access and the Streamlit UI renders model-provided Markdown/HTML so listing images can be displayed. A production deployment should restrict CORS origins and sanitize or whitelist rendered HTML.

---

## Future Improvements

A production iteration could add persistent booking storage, external scheduling integration, authentication, stronger schema validation, richer ranking, semantic/vector search, structured API error responses, rate limiting, centralized telemetry, and a more robust frontend security model.

The preprocessing pipeline could also be extended with manually reviewed labels or confidence scores so enrichment quality can be measured directly rather than inferred from field coverage alone.

---

## Demonstration

### 1. Multi-turn inventory conversation

The first demo shows the agent maintaining context while exploring the vehicle inventory across multiple messages.
<img width="1906" height="1014" alt="image" src="https://github.com/user-attachments/assets/22f15813-a714-4732-a3bf-2653604e4f73" />
<img width="1904" height="1028" alt="image" src="https://github.com/user-attachments/assets/77bbe1c1-e71d-4bec-8a2d-91c266e8ee75" />
<img width="1910" height="1031" alt="image" src="https://github.com/user-attachments/assets/76b4608d-41ff-405b-b068-f657e95a1a34" />
<img width="1908" height="1018" alt="image" src="https://github.com/user-attachments/assets/073e3f8c-1438-40df-9e8b-ff56cfe320f9" />





### 2. Memory across a new session

The second demo shows that user information stored during one session can be recalled after starting a completely new session.

<img width="1888" height="1016" alt="image" src="https://github.com/user-attachments/assets/ed93a586-8f22-468a-8f36-c9de966cc6ce" />
<img width="1886" height="1026" alt="image" src="https://github.com/user-attachments/assets/183efbc6-3c7f-478a-ab3a-89b4a0d8e929" />
<img width="1908" height="1024" alt="image" src="https://github.com/user-attachments/assets/3548694e-00ad-428b-a6c7-5a5eb6d68adb" />
<img width="1896" height="1018" alt="image" src="https://github.com/user-attachments/assets/b1577930-99f9-4f26-bae2-307ad471d76d" />

