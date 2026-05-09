type Props = {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
}

export default function Pagination({ total, limit, offset, onChange }: Props) {
  const page = Math.floor(offset / limit) + 1
  const pageCount = Math.max(1, Math.ceil(total / limit))
  const prev = () => onChange(Math.max(0, offset - limit))
  const next = () => onChange(Math.min(offset + limit, (pageCount - 1) * limit))

  return (
    <div className="flex items-center justify-between mt-4 text-sm">
      <div className="text-base-content/60">
        {total === 0
          ? 'No rows'
          : `${offset + 1}–${Math.min(offset + limit, total)} of ${total.toLocaleString()}`}
      </div>
      <div className="join">
        <button
          className="join-item btn btn-sm"
          onClick={prev}
          disabled={offset === 0}
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
