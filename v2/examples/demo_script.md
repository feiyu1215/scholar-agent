# Demo Recording Script

Instructions for recording a 2-3 minute asciinema demo showing ScholarAgent's full pipeline.

## Prerequisites

```bash
# Install asciinema (recording) + agg (GIF conversion)
brew install asciinema
cargo install --git https://github.com/asciinema/agg  # or download binary
```

## Recording Plan

### Scene 1: Start & Parse (0:00–0:20)

```bash
# Start recording
asciinema rec demo.cast --title "ScholarAgent v4 Demo" --cols 120 --rows 35

# Run the agent
python3 main.py --stream --budget full --paper examples/sample_paper.md
```

**What to show:** Agent auto-enters PARSE phase, calls `parse_paper`, outputs section index.

### Scene 2: Review (0:20–0:50)

**What to show:**
- Agent transitions to REVIEW phase
- Calls `review_paper` — 5 reviewers run in parallel
- Outputs consolidated review: 10 issues, severity breakdown
- Shows action routing: 4 auto_fix, 3 confirm_fix, 3 guidance

### Scene 3: Revise + De-AI (0:50–1:40)

**What to show:**
- Agent enters REVISE phase
- First `auto_fix` promoted to `confirm_fix` (first-of-type rule)
- User approves (via `/resume` or direct confirmation)
- `rewrite_section` executes → De-AI audit triggers automatically
- Shows FAIL → fix → re-audit → PASS cycle (PEV Loop in action)

### Scene 4: Streaming Control (1:40–2:00)

**What to show:**
- Type `/pause` during streaming → agent pauses mid-thought
- Type `/resume` → agent continues
- Demonstrates human-in-the-loop interactivity

### Scene 5: Summary (2:00–2:30)

**What to show:**
- Agent reaches COMPLETE phase
- Outputs: score progression (4.5 → 6.5), issues resolved, token usage
- Clean exit

## Post-Recording

```bash
# Convert to GIF (for README)
agg demo.cast demo.gif --font-size 14 --speed 1.5

# Or convert to SVG (sharper, smaller file)
agg demo.cast demo.svg --font-size 14 --speed 1.5

# Place in project
mv demo.gif assets/demo.gif
```

Then add to README.md:
```markdown
![ScholarAgent Demo](assets/demo.gif)
```

## Tips for Good Recording

1. **Pre-load the model** — run once before recording so API is warmed up
2. **Use `--stream`** — shows real-time token output (more visually engaging)
3. **Terminal setup** — dark theme, no distracting prompts, large font
4. **Speed up pauses** — agg `--speed 1.5` handles waiting for API responses
5. **Clear screen before recording** — `clear && printf '\e[3J'`

## Fallback: Static Demo

If live recording is impractical (API cost, timing issues), use the pre-captured outputs in `examples/demo_output/` as a "paper trail" demo. The `before_after_diff.md` and `score_progression.md` files tell the story visually.
