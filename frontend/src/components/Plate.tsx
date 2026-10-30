import { useCallback, useRef, useState, type ReactNode } from 'react'

export interface PlateSide {
  src?: string
  caption: string
}

interface Props {
  left: PlateSide
  right: PlateSide
  /** A transparent layer drawn over `left.src` (used for the change layer). Disables comparison. */
  overlay?: string
  legend?: ReactNode
}

/** Before/after viewer in a survey-plate frame: draggable slider or side by side. */
export default function Plate({ left, right, overlay, legend }: Props) {
  const [mode, setMode] = useState<'slider' | 'side'>('slider')
  const [pos, setPos] = useState(50)
  const box = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const move = useCallback((clientX: number) => {
    const el = box.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setPos(Math.max(0, Math.min(100, ((clientX - r.left) / r.width) * 100)))
  }, [])

  const missing = !left.src || !right.src

  return (
    <figure className="m-0">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        {overlay ? (
          <span className="text-sm text-ink-soft">Change between the earlier and newer image</span>
        ) : (
          <div role="group" aria-label="View mode" className="inline-flex overflow-hidden rounded-ui border border-ink">
            {(['slider', 'side'] as const).map((m) => (
              <button
                key={m}
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
                className={`px-3 py-1 text-sm font-medium ${mode === m ? 'bg-ink text-white' : 'bg-panel hover:bg-white'}`}
              >
                {m === 'slider' ? 'Compare' : 'Side by side'}
              </button>
            ))}
          </div>
        )}
        {legend}
      </div>

      {missing ? (
        <div className="plate"><div className="plate-inner grid h-64 place-items-center text-sm text-white/80" style={{ lineHeight: 1.5 }}>Images are not available yet.</div></div>
      ) : overlay ? (
        <div className="plate">
          <div className="plate-inner">
            <img src={left.src} alt="Earlier image of the site" className="block h-auto w-full" />
            <img src={overlay} alt="Detected change drawn over the image" className="absolute inset-0 h-full w-full" />
          </div>
        </div>
      ) : mode === 'side' ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[left, right].map((s, i) => (
            <div key={i}>
              <div className="plate"><div className="plate-inner"><img src={s.src} alt={`${i === 0 ? 'Earlier' : 'Newer'} image of the site`} className="block h-auto w-full" /></div></div>
              <figcaption className="mt-2 text-sm text-ink-soft">{s.caption}</figcaption>
            </div>
          ))}
        </div>
      ) : (
        <div className="plate">
          <div
            ref={box}
            className="plate-inner cursor-ew-resize touch-none select-none"
            onPointerDown={(e) => {
              dragging.current = true
              e.currentTarget.setPointerCapture(e.pointerId)
              move(e.clientX)
            }}
            onPointerMove={(e) => dragging.current && move(e.clientX)}
            onPointerUp={() => (dragging.current = false)}
            onPointerCancel={() => (dragging.current = false)}
          >
            <img src={right.src} alt="Newer image of the site" draggable={false} className="block h-auto w-full" />
            <img
              src={left.src}
              alt="Earlier image of the site"
              draggable={false}
              className="absolute inset-0 h-full w-full"
              style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}
            />
            <div className="slider-handle" style={{ left: `${pos}%` }} />
          </div>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(pos)}
            onChange={(e) => setPos(Number(e.target.value))}
            aria-label="Position of the divider between the earlier and newer image"
            className="mt-3 w-full accent-contour"
          />
          <div className="mt-1 flex justify-between text-sm text-ink-soft">
            <span>{left.caption}</span>
            <span className="text-right">{right.caption}</span>
          </div>
        </div>
      )}
    </figure>
  )
}
