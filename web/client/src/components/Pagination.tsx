type Props = {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
}

/**
 * Page counter and prev/next for an offset-paginated list.
 *
 * The offset is clamped to the data before anything is rendered from it. That
 * used to be unnecessary — the only way to move was these two buttons, which
 * cannot overshoot. Once the results page started reading its offset from the
 * query string, a stale or hand-edited link could, and the summary line happily
 * read "26–7 of 7 · page 2 / 1".
 */
export default function Pagination({ total, limit, offset, onChange }: Props) {
  const pageCount = Math.max(1, Math.ceil(total / limit))
  const lastOffset = (pageCount - 1) * limit
  const safeOffset = Math.min(Math.max(0, offset), lastOffset)
  const page = Math.floor(safeOffset / limit) + 1

  const prev = () => onChange(Math.max(0, safeOffset - limit))
  const next = () => onChange(Math.min(safeOffset + limit, lastOffset))

  // An offset past the end returns no rows, so say that rather than invent a range.
  const beyondEnd = offset > lastOffset && total > 0

  return (
    <div className="flex items-center justify-between mt-4 text-sm">
      <div className="text-base-content/60">
        {total === 0 && 'No rows'}
        {total > 0 && beyondEnd && (
          <>
            Past the last page of {total.toLocaleString()} —{' '}
            <button className="link" onClick={() => onChange(0)}>back to the first</button>
          </>
        )}
        {total > 0 && !beyondEnd
          && `${safeOffset + 1}–${Math.min(safeOffset + limit, total)} of ${total.toLocaleString()}`}
      </div>
      <div className="join">
        <button
          className="join-item btn btn-sm"
          onClick={prev}
          disabled={safeOffset === 0}
        >
          «
        </button>
        <span className="join-item btn btn-sm btn-ghost cursor-default">
          page {page} / {pageCount}
        </span>
        <button
          className="join-item btn btn-sm"
          onClick={next}
          disabled={page >= pageCount}
        >
          »
        </button>
      </div>
    </div>
  )
}
