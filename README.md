# ParcelPilot Customer Support AI Agent

A lightweight ParcelPilot support assistant for policy lookup, account/order/ticket inspection, and confirmation-based operational actions.

## Project structure

- `src/parcelpilot/` — application logic
  - `agent.py` — routes queries between document search, structured lookup, and action flows
  - `ingestion.py` — PDF document ingestion and metadata-aware retrieval
  - `structured_data.py` — workbook-based account/order/ticket data access with access control
  - `action_tool.py` — mocked operational actions with confirmation workflow
  - `llm_config.py` — optional Mistral provider configuration
- `ui/app.py` — Streamlit chat interface
- `tests/` — project tests
- `doc/` — source PDFs
- `excel/` — workbook helper code
- `.env` / `.env.example` — local environment settings

## Setup

1. Open the project folder.
2. Use the project virtual environment:

```powershell
cd "c:\Users\vansh\OneDrive\Desktop\project_new"
.\venv\Scripts\Activate.ps1
```

3. Install dependencies if needed:

```powershell
python -m pip install -r requirements.txt
```

4. Run the app:

```powershell
streamlit run ui/app.py
```

## Validate the project

```powershell
$env:PYTHONPATH = "src"
& "c:/Users/vansh/OneDrive/Desktop/project_new/venv/Scripts/python.exe" -m pytest tests -q
```

Expected result: all tests pass.

## Example prompts

- What is the return policy?
- Can Northstar cancel ORD-1001 without a cancellation fee?
- What is the status of order ORD-1001?
- Escalate ticket TKT-501 for a P1 outage.

## Notes

- The app is intentionally explainable and deterministic.
- It uses local, rule-based logic for safety and reliability.
- Mistral is optional and enabled through environment variables when configured.
- Retrieval is hybrid: keyword scoring merged with Qdrant vector search over
  `mistral-embed` embeddings (in-memory store by default; set `QDRANT_URL` for
  Qdrant Cloud). It degrades gracefully to keyword-only retrieval.
- LLM answers are grounded: Mistral only summarizes retrieved excerpts and
  never executes actions. Set `AGENT_MODE=langchain` to opt into an autonomous
  tool-calling agent (state changes still require explicit confirmation).
