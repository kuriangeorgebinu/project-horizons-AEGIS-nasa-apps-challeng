# AEGIS - Autonomous Expert Group for Integrated Space Health

**NASA Space Apps Challenge 2026**

A multi-agent AI system for autonomous crew health monitoring and decision support during deep space missions with communication delays.

---

## Quick Start

### Prerequisites

- **Python 3.10+** (Python 3.13 recommended)
- **uv** (Python package manager) - [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
  ```bash
  # Windows (PowerShell)
  irm https://astral.sh/uv/install.ps1 | iex
  
  # macOS/Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 1. Install Dependencies

```bash
cd C:\Users\kgeor\Desktop\NASA_Hackathon_Challenge

# Create virtual environment and install dependencies
uv sync
```

Or with pip:
```bash
# Create venv
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (macOS/Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Start the Backend API

```bash
# Using uv (recommended)
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Or if venv is activated
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

The backend will start at: **http://127.0.0.1:8000**

- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

### 3. Start the Frontend (Static Server)

Open a **new terminal** and run:

```bash
cd C:\Users\kgeor\Desktop\NASA_Hackathon_Challenge

# Using Python's built-in server
python -m http.server 3000 --bind 127.0.0.1
```

The frontend will be at: **http://127.0.0.1:3000/aegis-mission-control.html**

### 4. Open the Dashboard

Navigate to: **http://127.0.0.1:3000/aegis-mission-control.html**

You should see the AEGIS Mission Control dashboard with:
- 🟢 **Live** indicator (top-right) = API connected
- 🔴 **Disconnected** = Backend not running

---

## Project Structure

```
NASA_Hackathon_Challenge/
├── main.py                    # FastAPI backend server
├── models.py                  # Pydantic data models
├── database.py                # In-memory database
├── agents.py                  # Multi-agent AI system
├── aegis_rag.py               # RAG knowledge base
├── simulator.py               # Vitals simulation
├── setup_chromadb.py          # Knowledge base setup
├── aegis-mission-control.html # Main dashboard UI
├── aegis-api-client.js        # Frontend API client
├── aegis-spacecraft-sim-2d.html # 2D spacecraft visualization
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Project config (uv)
├── .env                       # Environment variables
├── aegis.db                   # SQLite database (auto-created)
├── chroma_db/                 # Vector database
├── data/                      # NASA data sources
│   ├── aegis_knowledge_base.json
│   ├── nasa_hrr_risks.json
│   ├── hrp_docs/              # NASA HRP PDFs
│   ├── live_feeds/            # Space weather data
│   └── osdr_metadata/         # OSDR datasets
└── artifacts/                 # Design documents
    ├── AEGIS-Development-Plan.html
    ├── AEGIS-Technical-Architecture.html
    └── aegis-solution-brief.html
```

---

## Configuration

### Environment Variables (.env)

```bash
# Server
HOST=127.0.0.1
PORT=8000

# Optional: LLM API Keys for enhanced recommendations
# OPENAI_API_KEY=sk-...
# DEEPSEEK_API_KEY=sk-...
```

### LLM Integration (Optional)

The system works without LLM keys using rule-based recommendations. To enable AI-enhanced recommendations:

1. Uncomment the desired API key in `.env`
2. Add your key
3. Restart the backend

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/crew` | Get all crew members |
| GET | `/crew/{id}` | Get crew member by ID |
| GET | `/recommendations` | Get all recommendations |
| POST | `/recommendations/{id}/action` | Accept/dismiss recommendation |
| GET | `/alerts` | Get active alerts |
| POST | `/query` | RAG knowledge base query |
| POST | `/analyze` | Trigger crew analysis |
| GET | `/simulation/state` | Get simulation state |
| POST | `/simulation/start` | Start vitals simulation |
| POST | `/simulation/stop` | Stop simulation |

Full API docs at: http://127.0.0.1:8000/docs

---

## Troubleshooting

### Backend won't start
```bash
# Check if port 8000 is in use
netstat -ano | findstr :8000

# Kill process using port (replace PID)
taskkill /PID <PID> /F
```

### Frontend shows "Disconnected"
1. Make sure backend is running on port 8000
2. Check browser console for CORS errors
3. Verify you're accessing via http://127.0.0.1:3000 (not localhost)

### Module not found errors
```bash
# Reinstall dependencies
uv sync --reinstall

# Or with pip
pip install -r requirements.txt --force-reinstall
```

### ChromaDB issues
```bash
# Rebuild the knowledge base
uv run python setup_chromadb.py
```

---

## Development

### Running Tests
```bash
uv run pytest
```

### Code Formatting
```bash
uv run black .
uv run ruff check .
```

---

## Architecture

AEGIS uses a multi-agent AI architecture with three pillars:

1. **PREDICT** - Forecasts health trends using ML models
2. **PREVENT** - Proactive countermeasures to avoid health issues
3. **RESPOND** - Emergency protocols when issues occur

Agents:
- **COORD** - Coordinator agent (orchestrates others)
- **VITALS** - Vital signs monitoring
- **PSYCH** - Psychological health
- **PHYSIO** - Physiological analysis
- **RAD** - Radiation exposure tracking
- **ENV** - Environmental monitoring

---

## License

NASA Space Apps Challenge 2026 - Open Source
