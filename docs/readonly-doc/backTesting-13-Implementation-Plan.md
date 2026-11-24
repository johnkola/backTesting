# backTesting: implementation plan

2026-11-23 19:21 Toronto time (step 0 added: the backend moves to Java, Node removed; Next.js and the schemas stay paused) · written by Claude (Claude Fable 5.2), for John · from John's direction of 2026-11-23 and `backTesting-00-Where-We-Are`

## What this application is for

A tool John runs himself, step by step: data, features, benchmarks and the engines (neural network, XGBoost, LightGBM), correct and ready to go, for any symbol and any timeframe (daily, hourly, 30 minutes).

It is not the coding agent's job to find a winning model. It is a sandbox: John can change any setting and rerun any step, or everything, from scratch, and what was stored is replaced.

## Not in this plan

- Next.js and the three schemas (stack steps 7 and 8). Paused until John says so. The Java backend is in the plan, as step 0.
- Running many combinations and comparing them in bulk. That is a different application.
- Users and login, load targets, publishing the signal, live sentiment, signal voting.

## Rules

1. Only John accepts a step. A step is done when he has run it himself in the interface and said so.
2. One step at a time, in order. The next step does not start until the current one is accepted.
3. On the current step the coding agent fixes only what John reports as wrong, or what stops him running the step.
4. An accepted step is not reopened unless John asks.
5. The coding agent (the agent run inside IntelliJ) never edits this plan or the design documents `backTesting-01` to `-04`, on Drive or in the repo. It records its work in its own tracker, `backTesting-07-Design-Gaps`.
6. Drive holds the master of this plan. Claude copies it one way into the repo for the coding agent to read. Claude records each acceptance here, on John's word.

## Steps

Step 0 comes first: the backend moves to Java (John, 2026-11-23: Java in the backend, no Node). The coding agent finishes moving the routes from Node to the Spring Boot service, the engine becomes the Java compute worker, and Node and FastAPI are removed (stack steps 4 to 6). Python still computes, and Java and Python talk through the jobs table. Done when the application runs with no Node and every page works as before; John accepts it. Steps 1 to 6 are then walked on QQQ, daily bars, with the current web client.

| # | Step | John does | Done when | Accepted |
| :---- | :---- | :---- | :---- | :---- |
| 1 | Data | Loads the price and input series, reads the check results, opens the bars on screen | The data is on screen, complete, and the checks say what is true. A missing bar is flagged and can be filled on the repair screen (from another source, a calculated value or by hand), with the repair logged. The calculation method is an option picked at repair time: rolled up from finer bars, a related index's move applied to the previous close, or the previous close carried forward; closed days are not gaps. Filling a missing bar is not built today (R89) |  |
| 2 | Features, normalized | Builds the features over the whole history the data has; looks at the values and their coverage | Every enabled feature is stored already normalized, over the full range, each bar scaled only against the bars before it, so any window chosen later is clean. A new bar is normalized as it arrives, and the values can be rebuilt and replaced on demand |  |
| 3 | Split | Sets the train, validation and test windows | The windows on screen are the ones used, with no overlap |  |
| 4 | Benchmarks | Runs buy-and-hold, Faber 10-month, dual momentum and the five indicator rules | Each figure can be checked by hand against the prices |  |
| 5 | Training | Starts training for each engine and watches it run | Each engine trains to the end and its run is recorded |  |
| 6 | Backtest and result | Runs the backtest, reads trades, equity curve and metrics beside the benchmarks | The trades and the profit figure can be followed and agree with the settings |  |
| 7 | Any symbol | Walks steps 1 to 6 on a second symbol of his choice | Nothing on the path assumes QQQ |  |
| 8 | Any timeframe | Walks steps 1 to 6 on hourly and on 30-minute bars | The same path runs on all three timeframes |  |

## Known before starting

- Step 2 changes the design. Today normalization is its own stage and the scaler is fitted on the training window, per fold (R68). John's decision of 2026-11-23: features are normalized once, over the full range, looking backwards only. R68, R79 and R84 are to follow.
- Step 7: the instrument features are tied to QQQ in the code (gap G56).
- Step 8: hourly and 30-minute bars have been run on synthetic data only; real intraday data has not been bought (gaps G22, G17).
- The profit figures leave out dividends, account currency and the superficial loss rule today. They are not in this plan until John adds them.

## Revision history

| When | Who | Change |
| :---- | :---- | :---- |
| 2026-11-23 19:03 | Claude (Claude Fable 5.2), for John | Normalization folded into the features step: full range, backward-looking only. Eight steps |
| 2026-11-23 18:58 | Claude (Claude Fable 5.2), for John | First version |
