/**
 * Form labels that carry their own explanation. Hovering the label text opens
 * the hint, so a field needs no separate marker beside it.
 *
 * The tip opens upward: to the right it would land on the neighbouring column's
 * control. daisyUI gives the bubble z-index 2, which the form controls can paint
 * over, so both the bubble and its tail are lifted above them here, and the text
 * is capped and wrapped rather than run out as one long line.
 */

/**
 * The tip itself, for the cases that have no label of their own to hang it on —
 * a checkbox row, or a filter input that carries only a placeholder. Put it on
 * the element that wraps the control, with `data-tip`: daisyUI opens on :hover
 * and on :has(:focus-visible), so the control's own focus ring brings the hint
 * up with it and no extra tab stop is needed.
 */
export const tipClass =
  'tooltip tooltip-top font-normal normal-case [&:after]:z-30 [&[data-tip]:before]:z-30 ' +
  '[&[data-tip]:before]:max-w-60 [&[data-tip]:before]:whitespace-normal [&[data-tip]:before]:text-left'

type Props = {
  text: string
  hint: string
  /** Extra classes for the label text — `text-xs` on the compact forms. */
  className?: string
  /** Omit the wrapping `label` row, for callers that supply their own. */
  bare?: boolean
}

/**
 * A label whose text is the hint's trigger. The text takes a tabIndex — without
 * it the hint would be mouse-only, since the text is not otherwise focusable —
 * and repeats itself in aria-label for readers that never see the bubble.
 */
export default function FieldLabel({ text, hint, className = '', bare = false }: Props) {
  const labelled = (
    <span className={tipClass} data-tip={hint}>
      <span
        tabIndex={0}
        role="note"
        aria-label={hint}
        className={`label-text cursor-help underline decoration-dotted decoration-base-content/30
                    underline-offset-4 transition-colors hover:decoration-base-content/70
                    focus-visible:outline-none focus-visible:decoration-base-content/70 ${className}`}
      >
        {text}
      </span>
    </span>
  )

  return bare ? labelled : <div className="label">{labelled}</div>
}
