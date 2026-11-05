/**
 * Every tooltip in the app, in one place.
 *
 * Architecture.md §8.2a ("Tooltips everywhere"): "Every label, button, column
 * header, tab title and form field carries a short description shown on hover
 * (and on focus for keyboard users). Descriptions live in one dictionary keyed
 * by element id, not inline in components, so they can be reviewed and edited
 * in one place."
 *
 * Keys are `area.field`, so a hint can be found from the screen it appears on.
 * `metricHints.ts` and `cohesionHints.ts` predate this and cover their own
 * domains; new hints belong here.
 *
 * These are explanations, not restatements. A field labelled "Epochs" whose
 * tooltip says "the number of epochs" has cost the reader a hover for nothing;
 * each one below says what the knob does and what going wrong looks like.
 */

export const fieldHints: Record<string, string> = {
  // --- Strategy: the twelve hyperparameters -------------------------------
  // The network is an MLP over a flattened window, so the first two decide the
  // shape of its input and everything else decides how it is fitted.
  'param.lookbackWindow':
    'How many bars of history each training sample sees. The network reads a '
    + 'flat vector, so this multiplies the input width: 29 features over 20 bars '
    + 'is 580 inputs. More context also means more weights to fit from the same '
    + 'number of samples, and it eats bars off the front of every window — a '
    + 'lookback of 200 cannot produce a single sample from a 150-bar window.',
  'param.forwardBars':
    'How far ahead the forward return is measured to label a bar buy, sell or '
    + 'hold. This is the horizon the model is actually predicting: 5 asks "where '
    + 'is price in a week", not "where is it tomorrow". It also trims the end of '
    + 'the training window, because the last few bars have no known future yet.',
  'param.buyThreshold':
    'Forward return above which a bar is labelled BUY. With 0.02, the model is '
    + 'taught to call a move only when it expects more than +2% over the '
    + 'horizon. Raise it and labels get rarer but cleaner; lower it and the '
    + 'model learns to act on noise.',
  'param.sellThreshold':
    'Forward return below which a bar is labelled SELL. Negative, and the band '
    + 'between it and the buy threshold is HOLD. Both at zero collapses that '
    + 'band — every bar becomes buy or sell on the sign of its return alone, and '
    + 'the model is taught to always hold a position.',
  'param.hiddenLayerSize':
    'Neurons in each hidden layer. The main dial on how much the network can '
    + 'memorise: too small and it cannot represent the pattern, too large and it '
    + 'learns the training years by heart and falls apart on the validation '
    + 'folds. Watch the gap between training and validation accuracy.',
  'param.numHiddenLayers':
    'How many hidden layers are stacked. Depth buys interaction between '
    + 'features rather than raw capacity; on tabular data of this size two is '
    + 'usually where the returns stop.',
  'param.dropoutRate':
    'Fraction of neurons randomly zeroed on each training pass, which stops the '
    + 'network leaning on any single feature. The cheapest defence against '
    + 'memorising a bull market. 0.5 is aggressive and appropriate here, because '
    + 'there are only a few thousand training samples.',
  'param.learningRate':
    'How far the weights move on each update. Too high and the loss curve '
    + 'oscillates or returns NaN; too low and training stops improving before '
    + 'it has learned anything. If the loss is not falling, this is the first '
    + 'knob to check.',
  'param.numEpochs':
    'Full passes over the training set. More is not better: past the point where '
    + 'validation loss turns back up, every extra epoch is the model memorising '
    + 'the training years. The Training tab is where that turn is visible.',
  'param.batchSize':
    'Samples averaged into each weight update. Small batches add noise that '
    + 'often helps generalisation but train slower; large ones are faster and '
    + 'smoother, and can settle into a worse answer.',
  'param.trainSplitRatio':
    'Share of the training window used to fit, with the rest held back to watch '
    + 'for overfitting while training runs. This is inside the training window '
    + 'only — it is not the walk-forward validation folds, which are separate '
    + 'bars the weights never see.',
  'param.seed':
    'Seeds the initial weights, the dropout mask and the shuffling. Fix it and '
    + 'the same configuration retrains to the same model, which is what makes '
    + 'two runs comparable. Vary only this to see how much of a result was luck.',

  // --- Strategy: the rest of the builder ---------------------------------
  'strategy.name':
    'How you will refer to it on the Train and Run pages. Saving an existing '
    + 'name stores a new version of it rather than replacing it, so a run always '
    + 'points at exactly the configuration it was trained from.',
  'strategy.notes':
    'Optional. What this version changes, so a list of versions reads as a '
    + 'record of experiments rather than a pile of names.',
  'strategy.instrument':
    'No screen assumes QQQ — the same form serves any instrument with stored '
    + 'bars. Instrument-scoped features follow this choice; market-wide ones '
    + '(rates, volatility, breadth) stay selected either way.',
  'strategy.source':
    'Which price history to learn from. Two sources for one symbol are '
    + 'different histories — yahoo and yahoo-adj differ by every dividend — and '
    + 'a model is tied to the one it trained on.',
  'strategy.timeframe':
    'Bar size. Daily now; hourly once intraday data is bought, through the same '
    + 'form and the same pipeline.',
  'strategy.modelType':
    'Populated from what the model layer registers, so a new architecture '
    + 'appears here without a UI change and the form can never offer something '
    + 'the backend cannot run.',
  'strategy.validationScheme':
    'Walk-forward trains on one window and tests on the next, then slides '
    + 'forward. The design\'s choice: judging a model on a single year is luck, '
    + 'and holding up across calm, choppy and falling years is not.',
  'strategy.labelHorizon':
    'Bars ahead the forward return is measured over to label each bar. The same '
    + 'horizon as Forward bars, and the thing the model is being asked to '
    + 'predict.',
  'strategy.features':
    'The exact columns the network sees. Recorded with the run, so two runs with '
    + 'different feature lists are two different experiments rather than one '
    + 'unexplained difference.',

  // --- Features tab ------------------------------------------------------
  'features.catalogueCount':
    'Every column the pipeline can produce, including the ones that are off. A '
    + 'disabled column is a recorded decision, not an absence, so it keeps its '
    + 'place in the table with the reason attached.',
  'features.active':
    'Usable now, and the only ones the strategy builder offers.',
  'features.undecided':
    'Defined but not fed to the model — a history too short to be worth it, or a '
    + 'decision deferred until the free columns are proven. Nothing about them is '
    + 'impossible.',
  'features.blocked':
    'No free source at full history. The design treats this as a hard stop '
    + 'rather than a deferral: the free endpoint truncates the series, so the '
    + 'column cannot be built honestly without paying.',
  'features.inputWidth':
    'Values per bar the network actually eats: enabled columns times their '
    + 'width, so each cyclical calendar column counts twice (sin and cos). '
    + 'Multiply by the lookback window to get the input size.',
  'features.set':
    'Coverage is measured against a built feature set, because "does this column '
    + 'have values" is a question about stored data. Pick the set whose '
    + 'instrument and timeframe you care about.',
  'features.window':
    'Narrow the coverage figures to a date range — the train window, say, or the '
    + 'holdout. Coverage is counted in BARS the set actually has, not calendar '
    + 'days: a one-year window holds about 252 trading days.',
  'features.search':
    'Matches the name, the normalization method, or the note.',
  'features.width':
    'This column emits more than one value per bar. A cyclical calendar column '
    + 'becomes sin and cos, so the cycle is continuous and December 31 sits next '
    + 'to January 1.',
  'features.paid':
    'No free history. Every free column is built and validated before any money '
    + 'is spent, so these wait.',
  'features.col.name': 'The column name, as the strategy builder and the model see it.',
  'features.col.scope':
    'instrument — computed from the bars of the chosen instrument, and follows it '
    + 'when that changes. market — shared across instruments, so it stays '
    + 'selected either way.',
  'features.col.units':
    'Units of the RAW series, not of the normalized output. It is what makes the '
    + 'right method obvious: a yield already in percent wants a daily change, not '
    + 'a daily return.',
  'features.col.method':
    'How the column is made scale-free and stationary. Every method is causal — '
    + 'a row depends only on the past of that column — which is what makes the '
    + 'output safe to precompute and store.',
  'features.col.cadence':
    'The native publication rate of the series. A run at a finer timeframe carries a '
    + 'coarser feature forward from its publication timestamp, so no bar ever '
    + 'sees a value before it was knowable.',
  'features.col.status':
    'active | undecided | blocked. Only active columns are offered when building '
    + 'a strategy.',
  'features.col.range':
    'The range this feature has on its OWN, read back from the stored values — not '
    + 'the overlap of the table. Features do not share one range, and the table is '
    + 'never trimmed to the shortest or padded to the longest.',
  'features.col.coverage':
    'Share of the bars in the window where this column has a number. Green has a '
    + 'value; amber has a row but nothing computable yet (a rolling window not '
    + 'full); the remainder has no row at all. Click a row for the gaps.',
  'features.col.lineage':
    'Fingerprint of how the column is computed — its method, parameters and '
    + 'source series. Two runs can be compared by which of their features were '
    + 'computed differently.',

  'strategy.featuresNotYetUsed':
    'Recorded with the strategy, but not yet consumed by training. '
    + 'nn-feedforward computes its own fixed 12 features from the candles and '
    + 'takes no feature list; the stored-feature trainer uses the whole set and '
    + 'has no subset option. Selecting columns here is therefore a declaration '
    + 'of intent until one of those learns to honour it.',

  // --- Train -------------------------------------------------------------
  'train.savedStrategy':
    'Every strategy saved on the Strategy tab, every version of each. Picking '
    + 'one fills the series and hyperparameters from what was saved, so the run '
    + 'is explainable afterwards. Leave it on "none" to train the series by '
    + 'hand, which is the old behaviour and records no feature list.',

  'train.readOnly':
    'A saved strategy has already decided the instrument, the series and the '
    + 'hyperparameters, so Train shows them rather than asking again. Change '
    + 'them on the Strategy tab, which stores a new version — that way the run '
    + 'always points at exactly what it was trained from.',

  'run.savedStrategy':
    'Every strategy saved on the Strategy tab. Picking one fills the series, the '
    + 'parameters and the scoring window from the record, so the backtest is of '
    + 'that strategy rather than of whatever the form happened to hold. The '
    + 'window defaults to its test block, which was held out of training.',

  // --- Strategy: the four windows ----------------------------------------
  'window.train': 'The bars the weights are fitted on.',
  'window.validation':
    'Bars used for early stopping and hyperparameter choice, never for fitting.',
  'window.test':
    'Bars held out and scored once, after training. Looking at these while '
    + 'tuning quietly turns them into training data.',
  'window.prediction':
    'A forward window the trained model is asked about — typically the most '
    + 'recent bars or the present. Optional.',
}

/**
 * The hint for a key, or a usable fallback.
 *
 * Returns an empty string rather than the key when nothing is registered: a
 * tooltip reading `param.newKnob` is worse than no tooltip, and an empty one
 * does not open. Hints are reviewed in this file, so a missing one is a gap to
 * fill here rather than something for a component to invent.
 */
export function hintFor(key: string, fallback = ''): string {
  return fieldHints[key] ?? fallback
}

/**
 * A hyperparameter's hint, with its bounds appended.
 *
 * The bounds come from the API's own `paramSpec`, which is the same spec the
 * validator enforces, so the tooltip cannot promise a range the server would
 * reject.
 */
export function paramHint(
  key: string,
  spec: { kind: 'int' | 'float', min: number, max: number },
): string {
  const kind = spec.kind === 'int' ? 'Whole number' : 'Number'
  const bounds = `${kind} between ${spec.min} and ${spec.max}. Blank uses the model default.`
  const hint = fieldHints[`param.${key}`]
  return hint ? `${hint}\n\n${bounds}` : bounds
}
