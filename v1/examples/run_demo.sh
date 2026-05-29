#!/bin/bash
# ScholarAgent Demo Script
# Runs the agent on a sample paper in review-only mode (zero rewrite cost)
#
# Prerequisites:
#   - Python 3.10+
#   - pip install -r requirements.txt
#   - .env file with API key configured
#
# Usage:
#   chmod +x examples/run_demo.sh
#   ./examples/run_demo.sh              # synthetic paper (fast, free-ish)
#   ./examples/run_demo.sh economics    # real DiD paper (Hui et al. 2023)
#   ./examples/run_demo.sh rdd          # real RDD paper (Hyman et al. 2022)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Select paper based on argument
PAPER_ARG="${1:-synthetic}"
case "$PAPER_ARG" in
    economics|econ|did)
        PAPER="examples/sample_paper_economics.pdf"
        PAPER_DESC="Hui et al. (2023) — AI & Employment (DiD)"
        ;;
    rdd|nber)
        PAPER="examples/sample_paper_rdd.pdf"
        PAPER_DESC="Hyman et al. (2022) — Hiring Subsidies (RDD)"
        ;;
    *)
        PAPER="examples/sample_paper.md"
        PAPER_DESC="Synthetic paper (intentional issues)"
        ;;
esac

echo "╔══════════════════════════════════════════════════════╗"
echo "║          ScholarAgent v4 — Demo Mode                ║"
echo "║                                                      ║"
echo "║  Paper:  $PAPER_DESC"
echo "║  Budget: minimal (review-only, no rewrites)          ║"
echo "║  Model:  default (from .env)                         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check prerequisites
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found. Copy .env.example and add your API key."
    echo "   cp .env.example .env"
    exit 1
fi

if ! python3 -c "import dotenv" 2>/dev/null; then
    echo "❌ Error: Dependencies not installed. Run:"
    echo "   pip install -r requirements.txt"
    exit 1
fi

if [ ! -f "$PAPER" ]; then
    echo "❌ Error: Paper file not found: $PAPER"
    exit 1
fi

echo "🚀 Starting ScholarAgent in review-only mode..."
echo "   (The agent will review the paper and output guidance without making changes)"
echo ""
echo "─────────────────────────────────────────────────────"
echo ""

# Run in minimal budget (review-only) mode
python3 main.py --budget minimal --paper "$PAPER"

echo ""
echo "─────────────────────────────────────────────────────"
echo ""
echo "✅ Demo complete!"
echo ""
echo "To try full revision mode (with rewrites + De-AI audit):"
echo "   python3 main.py --budget full --paper $PAPER"
echo ""
echo "To try streaming mode with real-time control:"
echo "   python3 main.py --stream --budget full --paper $PAPER"
echo "   (Commands during streaming: /pause, /resume, /takeover)"
