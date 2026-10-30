import { useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { setAccessKey, useAccessKey } from '../services/api'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded-ui text-sm font-medium ${isActive ? 'bg-contour text-white' : 'text-ink-soft hover:text-ink hover:bg-white/60'}`

export function AccessKeyButton() {
  const key = useAccessKey()
  const dialog = useRef<HTMLDialogElement>(null)
  const [draft, setDraft] = useState('')
  const qc = useQueryClient()

  const save = () => {
    setAccessKey(draft.trim())
    setDraft('')
    qc.invalidateQueries()
    dialog.current?.close()
  }

  return (
    <>
      {key ? (
        <span className="flex items-center gap-2 text-sm text-ink-soft">
          <span className="inline-block size-2 rounded-full bg-gain" aria-hidden />
          Access key set
          <button
            className="underline underline-offset-2 hover:text-ink"
            onClick={() => {
              setAccessKey('')
              qc.invalidateQueries()
            }}
          >
            Clear
          </button>
        </span>
      ) : (
        <button
          className="rounded-ui border border-ink px-3 py-1.5 text-sm font-medium hover:bg-white/70"
          onClick={() => dialog.current?.showModal()}
        >
          Enter access key
        </button>
      )}
      <dialog ref={dialog} className="m-auto w-[min(26rem,calc(100vw-2rem))] rounded-ui border border-ink bg-panel p-6 text-ink">
        <form
          method="dialog"
          onSubmit={(e) => {
            e.preventDefault()
            save()
          }}
        >
          <h2 className="text-lg font-semibold">Access key</h2>
          <p className="mt-2 text-sm text-ink-soft">
            Creating investigations and running analyses needs the access key from the project README. Without it you can
            still open the public sample.
          </p>
          <label className="mt-4 block text-sm font-medium" htmlFor="access-key">
            Key
          </label>
          <input
            id="access-key"
            type="password"
            autoComplete="off"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2"
          />
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" className="rounded-ui px-3 py-2 text-sm hover:bg-paper" onClick={() => dialog.current?.close()}>
              Cancel
            </button>
            <button type="submit" disabled={!draft.trim()} className="rounded-ui bg-contour px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
              Save key
            </button>
          </div>
        </form>
      </dialog>
    </>
  )
}

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-rule bg-panel">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2 text-lg font-bold tracking-tight">
            <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
              <rect width="32" height="32" rx="6" fill="#1F6B66" />
              <path d="M6 22c4-1 5-6 9-6s4 5 11 4M6 16c3-1 5-5 9-5s5 4 11 3" fill="none" stroke="#EDF0EE" strokeWidth="2" strokeLinecap="round" />
            </svg>
            GreenWatch
          </NavLink>
          <nav className="flex gap-1" aria-label="Main">
            <NavLink to="/" end className={navClass}>Investigations</NavLink>
            <NavLink to="/new" className={navClass}>New investigation</NavLink>
            <NavLink to="/tasks" className={navClass}>Inspection tasks</NavLink>
          </nav>
          <div className="ml-auto"><AccessKeyButton /></div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-10 text-sm text-ink-soft sm:px-6">
        GreenWatch reports potential change in detected pixels. A person must review every finding before action.
      </footer>
    </div>
  )
}
