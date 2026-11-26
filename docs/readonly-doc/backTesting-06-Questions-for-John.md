# backTesting: Questions for John

Nov 16, 2026 · @John

> **Revision 2026-11-22-2 · 2026-11-22 22:35 Toronto time · last edited by** Claude (Claude Fable 5.2) · [revision history](#revision-history)

**All 14 were answered by the author on 2026-11-19**, one at a time, and C1, C4 and C5 again on 2026-11-22 (the newer answers stand); the answers are in the last column, and the work that follows from them is in `backTesting-07-Design-Gaps` ("Decisions and work order, 2026-11-19"). They were: 8 design-vs-code conflicts, 3 feature calls, 3 about the requirements doc. Type your answer in the last column of each table.

## Design vs code conflicts

From *Decisions needed* in `backTesting-05-Requirements-Consolidated` (revision 2026-11-16-1). Until each is answered, the design and the code say different things.

| \# | Question | A: design says | B: code says | You said earlier | Blocks | Your answer |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| C1 | When does the stack move to the target? | Spring Boot single backend, Next.js, three schemas with roles, jobs table, no direct calls | Node API \+ Java engine \+ Python FastAPI over HTTP, one schema, one superuser; new work still lands there | Nov 1: Java is the only backend, Node API retired. No date given | R6, R48, R60, R67, R70 | **Move now, before further feature work (2026-11-22). Replaces the 2026-11-19 answer: after phase two works on real data** |
| C2 | Do data checks stop the chain? | Two or more flags on a row \= error \= hard stop | Architecture §3.2: findings never block the import | Nov 1: error \= hard stop, warning continues | R12, R65 | **A: errors stop, warnings continue** (2026-11-19) |
| C3 | Are nulls allowed in feature values? | No padding, no nulls | Warm-up and pre-start days stored as NULL, dropped at read | Oct 18: no null padding | R3 | **B: keep NULLs, dropped at read** (2026-11-19) |
| C4 | Keep the Deeplearning4j and ONNX check? | PyTorch vs DL4J vs ONNX Runtime in a Java compute worker | DL4J was deliberately removed | Nov 1: wanted the dual-library check | R62 | **Drop Deeplearning4j, keep ONNX Runtime as the prediction check (2026-11-22). Replaces the 2026-11-19 answer: drop it, PyTorch only** |
| C5 | TA-Lib or the hand-written indicators? | TA-Lib, one shared module | `nn/indicators.py` hand-written to match Ta4j bit for bit | Nov 1: TA-Lib, shared by features and benchmarks | R66 | **A: TA-Lib is the single source; the hand-written indicators stay only as the cross-check (2026-11-22). Replaces the 2026-11-19 answer: keep the hand-written indicators** |
| C6 | Walk-forward: expanding or sliding window? | Architecture §7.2a slides a fixed window by a chosen step | Folds always train from the start date, one-year step, no step setting (expanding) | Nothing on record | R25 | **Offer both; expanding stays the default** (2026-11-19) |
| C7 | Are RSI, MACD and realized volatility model inputs? | R21: MACD-style indicators become NN features | Design Notes table and Features: data only, kept out of the network | Oct 31: MACD-type indicators belong as NN features | R19, R21, R78 | **Selectable per strategy, off by default** (2026-11-19) |
| C8 | What happens to the five rule strategies (`sma-crossover`, `ema-triple`, `macd`, `rsi`, `bollinger`)? | R21: no rule-based strategy types | Engine and Operator Reference still ship all five | Oct 31: drop rule-based strategies | R21, R22 | **Keep as benchmarks only, not offered as strategies** (2026-11-19) |

Where your earlier position already matches column A, a plain "A" confirms it and the code gets changed to match.

## Feature calls

Three features had no decision in the notes from Oct 18\. This list was not re-checked against the current `backTesting-02-Features`, so strike any you have settled since.

| \# | Feature | Status on record | Question | Your answer |
| :---- | :---- | :---- | :---- | :---- |
| F1 | VIX term structure | Undecided | In the model, data only, or dropped? | **Selectable input, off by default** (2026-11-19) |
| F2 | QQQ/SPY relative strength | Undecided | In the model, data only, or dropped? | **Selectable input, off by default** (2026-11-19) |
| F3 | High-yield credit spread | Blocked | Find another source, or drop it? | **Drop it** (2026-11-19) |

## About the requirements doc

| \# | Question | Why it is open | Your answer |
| :---- | :---- | :---- | :---- |
| Q1 | Is the consolidated requirements doc approved to replace reading the four design docs and three trackers? | It is marked draft for review | **Approve, after re-checking the statuses** (2026-11-19) |
| Q2 | Should the status on each requirement be re-verified against the code? | Statuses were copied from `backTesting-07-Design-Gaps` Pass 4 and the `backTesting-12-Coding-Agent-Instructions` build table, not checked | **Yes (it is the condition on Q1)** (2026-11-19) |
| Q3 | Should C8 get a row in `backTesting-07-Design-Gaps`? | It is the only conflict that is not tracked there | **Yes: C8 is answered and gets its row** (2026-11-19) |

*Generated 2026-11-16 by Claude (Claude Fable 5.2) for John, from `backTesting-05-Requirements-Consolidated` revision 2026-11-16-1 and earlier session notes.*

## Revision history

| Revision | When | Who | Change |
| :---- | :---- | :---- | :---- |
| 2026-11-21-1 | 2026-11-21 17:54 | Claude Code (Claude Fable 5.2) | Renamed from `QUESTIONS-FOR-JOHN.md`; references to the other renamed docs follow |
| 2026-11-19-1 | 2026-11-19 12:48 | Claude Code (Claude Fable 5.2) | The author's 14 answers recorded |

Revision 2026-11-22-1 · 2026-11-22 19:00 Toronto time · Claude (Claude Fable 5.2), for John · Made self-contained: every pointer to a document outside the set now names a document inside it (backTesting-11-Operator-Reference or backTesting-12-Coding-Agent-Instructions) or was dropped, code paths are written from the project root, and dead links are plain text. Nothing else changed.  
Revision 2026-11-22-2 · 2026-11-22 22:35 Toronto time · Claude (Claude Fable 5.2), for John · C1, C4 and C5 answered again by the author on 2026-11-22; the new answers replace the 2026-11-19 ones in the last column.

John's answers, Nov 25, 2026 (by voice; written by Claude, Claude Opus 5.5)

1\. Deleting a saved strategy: deleting a strategy deletes everything related to it, its runs and results included.  
2\. SMA crossover benchmark (G79): both, 50/200 and 10/50, as two separate benchmarks.  
3\. Backtest results 1019 and 1028, and all other stored results: delete all old results. They were made under normalization and settings that have since changed, and every result can be recalculated cheaply.  
4\. Run 855: no longer a question; it is deleted with the other old results and judged again when rerun.  
