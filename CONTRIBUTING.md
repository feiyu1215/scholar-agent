# Contributing to ScholarAgent

Thank you for your interest in contributing! This document explains how to set up the development environment and the project's conventions.

---

## Project Structure

All active development happens in `v2/`. The root-level `v1/`, `legacy/`, and `poc/` directories are preserved for historical reference only.

```
v2/
├── main.py                 # CLI entry point
├── core/                   # Core engine (49 modules)
│   ├── agent.py            # ScholarAgent class
│   ├── loop.py             # Cognitive loop
│   ├── harness.py          # State management + tool execution
│   ├── consolidation.py    # Post-loop semantic deduplication
│   ├── phases.py           # Phase FSM
│   ├── identity.py         # Persona definitions + tool schemas
│   ├── godel_config.py     # Kill switches (29 feature flags)
│   ├── skills/             # Programmatic skills (economics, multimodal)
│   └── tool_handlers/      # Tool implementations (6 modules, 26 tools)
├── llm/                    # LLM client + model routing
├── evaluation/             # Gold standard evaluation
├── training/               # Adversarial training subsystem
├── skills/                 # Knowledge skills (markdown files)
└── config/                 # Configuration files
```

## Development Setup

```bash
git clone https://github.com/your-username/scholar-agent.git
cd scholar-agent
pip install -r v2/requirements.txt
pip install pytest pytest-asyncio ruff  # Dev dependencies
cp .env.example .env
# Edit .env with your API key
```

## Code Conventions

- **Type hints** on all function signatures
- **Async/await** for all LLM calls and I/O operations
- **Docstrings** on public classes and methods
- **No registry patterns** (except `skill_registry` for static knowledge)
- **No workflow frameworks** (LangChain, CrewAI, etc.) — this is a cognitive architecture

## Key Design Principles

Before making changes, read `docs/COGNITIVE_ANCHOR.md`. The core principles:

1. **Agent = Cognition, not Orchestration** — The model decides; the code executes
2. **Constrain, don't control** — Phase transitions are nudges, not blocks
3. **No theater code** — If it doesn't affect output quality, delete it
4. **Budget is invisible** — Agent never knows about token limits
5. **Kill switches on everything** — Every subsystem can be independently disabled

## Making Changes

1. **Read before writing**: Understand the module you're changing by reading its full source
2. **Respect kill switches**: New features should have a `SCHOLAR_GODEL_*` flag
3. **Test end-to-end**: Unit tests are secondary; E2E behavior is primary
4. **Don't break consolidation**: After changes, verify Recall doesn't drop

## Running Tests

```bash
cd v2/

# Import check (all 49 core modules)
python -c "import importlib, pathlib; [importlib.import_module(f'core.{f.stem}') for f in pathlib.Path('core').glob('*.py') if f.name != '__init__.py']"

# Evaluation (requires API key)
python -m evaluation.run_recall_verification --paper paper_001
```

## Pull Request Guidelines

- Describe what changed and why
- Include before/after metrics if touching core logic
- Ensure CI passes (lint + import check)
- One logical change per PR

---

## License

By contributing, you agree that your contributions will be licensed under GPL-3.0.
