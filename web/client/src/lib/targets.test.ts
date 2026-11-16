import { describe, expect, it } from 'vitest'
import type { LabelKindSpec } from './api'
import {
  CLASS_KIND, CLASS_ONLY, DEFAULT_LABEL_FORM, asTarget, classWeightsRefusal, fitObjectiveToTarget,
  kindTitle, labelKindsOf, labelLine, labelParamHint, labelPayload, labelProblems, labelToForm,
  lossRefusalForTarget, runLabel, runName, runTarget, scoreLabel, targetObjectiveProblems, targetOf,
  unregisteredKind,
} from './targets'

// The registry as GET /api/strategies/saved serves it (web/api/strategies.js
// LABEL_KINDS), in its order.
const kinds: Record<string, LabelKindSpec> = {
  forward_return_3class: { target: 'class', params: {} },
  next_bar_direction: {
    target: 'direction',
    params: {
      buyThreshold: { min: 0, max: 1, default: 0.55, label: 'P(up) that calls BUY' },
      sellThreshold: { min: 0, max: 1, default: 0.45, label: 'P(up) that calls SELL' },
    },
  },
  forward_return: {
    target: 'return',
    params: {
      minReturn: { min: 0, max: 0.99, default: 0, label: 'Forecast return a call must clear' },
    },
  },
}

describe('the kinds offered (G49)', () => {
  it('are the API\'s, or the 3-class label alone from an API that sent none', () => {
    expect(labelKindsOf({ labelKinds: kinds })).toBe(kinds)
    expect(labelKindsOf({})).toBe(CLASS_ONLY)
    expect(labelKindsOf({ labelKinds: {} })).toBe(CLASS_ONLY)
    expect(labelKindsOf(null)).toBe(CLASS_ONLY)
  })

  it('name each kind by its target in plain words', () => {
    expect(kindTitle('forward_return_3class', kinds)).toBe('3-class (±threshold over the horizon)')
    expect(kindTitle('next_bar_direction', kinds)).toBe('Direction (P(up))')
    expect(kindTitle('forward_return', kinds)).toBe('Return (forecast)')
  })

  it('add the kind\'s name only when two kinds share a target', () => {
    const two = { ...kinds, close_above_open: { target: 'direction' as const, params: {} } }
    expect(kindTitle('next_bar_direction', two)).toBe('Direction (P(up)) · next_bar_direction')
    expect(kindTitle('forward_return', two)).toBe('Return (forecast)')
  })
})

describe('targetOf / asTarget', () => {
  it('reads a kind\'s target from the registry', () => {
    expect(targetOf('next_bar_direction', kinds)).toBe('direction')
    expect(targetOf('forward_return', kinds)).toBe('return')
    expect(targetOf(CLASS_KIND, kinds)).toBe('class')
  })

  it('knows the registered kinds without the list, for a stored record', () => {
    expect(targetOf('forward_return')).toBe('return')
    expect(targetOf('next_bar_direction', null)).toBe('direction')
  })

  it('reads anything else as the 3-class label, as every run before G49 was', () => {
    expect(targetOf('something_new', kinds)).toBe('class')
    expect(targetOf(null)).toBe('class')
    expect(asTarget(undefined)).toBe('class')
    expect(asTarget('return')).toBe('return')
  })
})

describe('labelToForm', () => {
  it('starts a new strategy as the 3-class label over five bars, as the form always has', () => {
    expect(DEFAULT_LABEL_FORM).toEqual({ kind: CLASS_KIND, horizon: '5', params: {} })
  })

  it('loads a version from before labels were recorded as the 3-class label', () => {
    expect(labelToForm(null, kinds)).toEqual(DEFAULT_LABEL_FORM)
    expect(labelToForm({ kind: CLASS_KIND, horizonBars: 10 }, kinds))
      .toEqual({ kind: CLASS_KIND, horizon: '10', params: {} })
  })

  it('loads a saved kind\'s own thresholds as text, and only its own', () => {
    expect(labelToForm(
      { kind: 'next_bar_direction', horizonBars: 1, buyThreshold: 0.6, sellThreshold: 0.4, minReturn: 0.1 },
      kinds,
    )).toEqual({
      kind: 'next_bar_direction', horizon: '1', params: { buyThreshold: '0.6', sellThreshold: '0.4' },
    })
    expect(labelToForm({ kind: 'forward_return', horizonBars: 5, minReturn: 0 }, kinds).params)
      .toEqual({ minReturn: '0' })
  })

  it('falls back to the 3-class label for a kind the API no longer registers, and names it', () => {
    const gone = { kind: 'retired_kind', horizonBars: 3 }
    expect(labelToForm(gone, kinds)).toEqual({ kind: CLASS_KIND, horizon: '3', params: {} })
    expect(unregisteredKind(gone, kinds)).toBe('retired_kind')
    expect(unregisteredKind({ kind: 'forward_return', horizonBars: 3 }, kinds)).toBeNull()
    expect(unregisteredKind(null, kinds)).toBeNull()
  })
})

describe('labelPayload', () => {
  it('sends the 3-class label as it always did: kind and horizon alone', () => {
    expect(labelPayload(DEFAULT_LABEL_FORM, kinds)).toEqual({ kind: CLASS_KIND, horizonBars: 5 })
  })

  it('sends a blank threshold as its default and a typed one as a number', () => {
    expect(labelPayload({ kind: 'next_bar_direction', horizon: '1', params: { buyThreshold: ' 0.6 ' } }, kinds))
      .toEqual({ kind: 'next_bar_direction', horizonBars: 1, buyThreshold: 0.6, sellThreshold: 0.45 })
  })

  it('leaves out the thresholds of a kind not chosen', () => {
    const form = { kind: 'forward_return', horizon: '5', params: { buyThreshold: '0.7', minReturn: '0.001' } }
    expect(labelPayload(form, kinds)).toEqual({ kind: 'forward_return', horizonBars: 5, minReturn: 0.001 })
  })
})

describe('labelProblems', () => {
  it('passes the defaults of every kind', () => {
    for (const kind of Object.keys(kinds)) {
      expect(labelProblems({ kind, horizon: '5', params: {} }, kinds)).toEqual([])
    }
  })

  it('holds a horizon that is not a whole number of bars in bounds', () => {
    for (const horizon of ['', '0', '101', '2.5', 'x']) {
      expect(labelProblems({ ...DEFAULT_LABEL_FORM, horizon }, kinds)[0]).toMatch(/whole number of bars/)
    }
  })

  it('holds a threshold that is not a number or out of its bounds', () => {
    expect(labelProblems({ kind: 'forward_return', horizon: '5', params: { minReturn: 'abc' } }, kinds))
      .toEqual(['Forecast return a call must clear must be a number.'])
    expect(labelProblems({ kind: 'forward_return', horizon: '5', params: { minReturn: '1' } }, kinds))
      .toEqual(['Forecast return a call must clear must be between 0 and 0.99.'])
  })

  it('holds crossed direction thresholds, a tie included: HOLD sits between them', () => {
    const at = (buy: string, sell: string) => labelProblems(
      { kind: 'next_bar_direction', horizon: '1', params: { buyThreshold: buy, sellThreshold: sell } }, kinds,
    )
    expect(at('0.4', '0.6')[0]).toMatch(/SELL must be below P\(up\) that calls BUY/)
    expect(at('0.5', '0.5')).toHaveLength(1)
    expect(at('0.6', '')).toEqual([])   // blank is the default, 0.45
    expect(at('0.4', '')).toHaveLength(1)
  })

  it('holds a kind that is not registered', () => {
    expect(labelProblems({ kind: 'nope', horizon: '5', params: {} }, kinds)).toEqual([
      "Label kind 'nope' is not registered.",
    ])
  })

  it('gives each threshold\'s bounds and default in its hint', () => {
    expect(labelParamHint('minReturn', kinds.forward_return.params.minReturn))
      .toMatch(/between 0 and 0\.99\. Blank uses the default, 0\.$/)
  })
})

describe('the target against the objective (G49)', () => {
  it('refuses a money-trained loss with a direction or return target, never ce', () => {
    for (const loss of ['return', 'sharpe', 'calmar']) {
      expect(lossRefusalForTarget(loss, 'class')).toBeNull()
      expect(lossRefusalForTarget(loss, 'direction')).toMatch(/direction target is fitted by its own loss, ce/)
      expect(lossRefusalForTarget(loss, 'return')).toMatch(new RegExp(`'${loss}' loss trains a position`))
    }
    expect(lossRefusalForTarget('ce', 'return')).toBeNull()
  })

  it('refuses class weights with a return target only', () => {
    expect(classWeightsRefusal('return')).toMatch(/need a class label/)
    expect(classWeightsRefusal('direction')).toBeNull()
    expect(classWeightsRefusal('class')).toBeNull()
  })

  it('switches a money loss to ce when the target changes under it, and says so', () => {
    const r = fitObjectiveToTarget({ loss: 'sharpe', classWeights: false }, 'direction')
    expect(r.change).toEqual({ loss: 'ce' })
    expect(r.notice).toMatch(/^Loss switched from 'sharpe' to ce/)
  })

  it('switches class weights off for a return target, and says so', () => {
    const r = fitObjectiveToTarget({ loss: 'ce', classWeights: true }, 'return')
    expect(r.change).toEqual({ classWeights: false })
    expect(r.notice).toMatch(/^Class weights switched off/)
  })

  it('clears weights a money loss left hidden without calling it news', () => {
    const r = fitObjectiveToTarget({ loss: 'return', classWeights: true }, 'return')
    expect(r.change).toEqual({ loss: 'ce', classWeights: false })
    expect(r.notice).not.toMatch(/Class weights/)
  })

  it('changes nothing when the objective already fits', () => {
    expect(fitObjectiveToTarget({ loss: 'ce', classWeights: true }, 'direction'))
      .toEqual({ change: {}, notice: null })
    expect(fitObjectiveToTarget({ loss: 'calmar', classWeights: false }, 'class'))
      .toEqual({ change: {}, notice: null })
  })

  it('holds Save on what the API would refuse', () => {
    expect(targetObjectiveProblems({ loss: 'sharpe', classWeights: false }, 'return')).toHaveLength(1)
    expect(targetObjectiveProblems({ loss: 'ce', classWeights: true }, 'return')).toHaveLength(1)
    expect(targetObjectiveProblems({ loss: 'ce', classWeights: true }, 'direction')).toEqual([])
    expect(targetObjectiveProblems({ loss: 'sharpe', classWeights: true }, 'class')).toEqual([])
  })
})

describe('scoreLabel', () => {
  it('shows a class run\'s accuracy and a direction\'s hit rate as percentages', () => {
    expect(scoreLabel('class').header).toBe('Accuracy')
    expect(scoreLabel(undefined).format(0.6123)).toBe('61.2%')
    const d = scoreLabel('direction')
    expect(d.header).toBe('Hit rate')
    expect(d.format(0.5)).toBe('50.0%')
    expect(d.baselineWords).toBe('always up')
    expect(d.lowerIsBetter).toBe(false)
  })

  it('shows a return forecast\'s error as a plain number to four places, lower better', () => {
    const r = scoreLabel('return')
    expect(r.header).toBe('Error (MAE)')
    expect(r.format(0.012345)).toBe('0.0123')
    expect(r.format(0.0123)).not.toContain('%')
    expect(r.lowerIsBetter).toBe(true)
    expect(r.baselineWords).toBe('the historical mean return')
  })

  it('shows null as a dash, and takes the API\'s baseline words when given', () => {
    expect(scoreLabel('return').format(null)).toBe('—')
    expect(scoreLabel('class').format(undefined)).toBe('—')
    expect(scoreLabel('direction', { direction: 'always up (training)' }).baselineWords)
      .toBe('always up (training)')
  })
})

describe('a run\'s name, label and target', () => {
  const label = { kind: 'forward_return', horizonBars: 5, minReturn: 0.001 }

  it('reads the name from `label` and the target label from `labelSpec`', () => {
    expect(runName({ label: 'money-sharpe' })).toBe('money-sharpe')
    expect(runLabel({ labelSpec: label })).toBe(label)
    expect(runLabel({ labelSpec: null })).toBeNull()
    expect(runLabel({})).toBeNull()
  })

  it('reads a run from before G49 as the 3-class label', () => {
    expect(runTarget({})).toBe('class')
    expect(runTarget({ target: 'direction' })).toBe('direction')
  })
})

describe('labelLine', () => {
  it('says a direction label\'s P(up) thresholds', () => {
    expect(labelLine({ kind: 'next_bar_direction', horizonBars: 1, buyThreshold: 0.55, sellThreshold: 0.45 }, 'direction'))
      .toBe('Direction over 1 bar: P(up) at or above 0.55 calls BUY, at or below 0.45 SELL, HOLD between')
  })

  it('says a return label\'s minimum, both ways, with no stray sign on zero', () => {
    expect(labelLine({ kind: 'forward_return', horizonBars: 5, minReturn: 0.001 }, 'return'))
      .toBe('Return over 5 bars: a forecast at or above 0.001 calls BUY, at or below -0.001 SELL, HOLD between')
    expect(labelLine({ kind: 'forward_return', horizonBars: 5, minReturn: 0 }, 'return'))
      .toContain('at or above 0 calls BUY, at or below 0 SELL')
  })

  it('reads the 3-class label\'s thresholds from the hyperparameters when it has them', () => {
    const l = { kind: CLASS_KIND, horizonBars: 5 }
    expect(labelLine(l, 'class', { buyThreshold: 0.02, sellThreshold: -0.015 }))
      .toBe('3-class over 5 bars: a return at or above +2% is BUY, at or below −1.5% SELL, HOLD between')
    expect(labelLine(l, 'class', {})).toMatch(/± return thresholds in the hyperparameters$/)
  })
})
