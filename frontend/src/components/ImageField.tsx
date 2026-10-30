import { useEffect, useState } from 'react'
import type { Role } from '../types/api'

export interface ImageDraft {
  file: File | null
  date: string
  source: string
}

export const emptyDraft: ImageDraft = { file: null, date: '', source: '' }

/** File picker with a preview, capture date and source for one image. The capture date is when the image was taken, not when it is uploaded. */
export default function ImageField({
  role, value, onChange,
}: { role: Role; value: ImageDraft; onChange: (v: ImageDraft) => void }) {
  const [preview, setPreview] = useState<string | null>(null)
  useEffect(() => {
    if (!value.file) {
      setPreview(null)
      return
    }
    const url = URL.createObjectURL(value.file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [value.file])

  const label = role === 'earlier' ? 'Earlier image' : 'Newer image'
  return (
    <fieldset className="rounded-ui border border-rule bg-panel p-4">
      <legend className="px-1 text-sm font-semibold">{label}</legend>
      <label htmlFor={`file-${role}`} className="block text-sm font-medium">JPEG, PNG or TIFF, up to 15 MB</label>
      <input
        id={`file-${role}`}
        type="file"
        accept="image/jpeg,image/png,image/tiff"
        onChange={(e) => onChange({ ...value, file: e.target.files?.[0] ?? null })}
        className="mt-1 block w-full text-sm file:mr-3 file:rounded-ui file:border-0 file:bg-contour file:px-3 file:py-1.5 file:text-white"
      />
      {preview && <img src={preview} alt={`Preview of the ${role} image`} className="mt-3 max-h-48 w-full rounded-ui border border-rule object-contain bg-ink/5" />}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`date-${role}`} className="block text-sm font-medium">Capture date</label>
          <input id={`date-${role}`} type="date" value={value.date} onChange={(e) => onChange({ ...value, date: e.target.value })}
            className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
        </div>
        <div>
          <label htmlFor={`source-${role}`} className="block text-sm font-medium">Image source</label>
          <input id={`source-${role}`} value={value.source} onChange={(e) => onChange({ ...value, source: e.target.value })}
            placeholder="Drone survey, open aerial dataset…" maxLength={120}
            className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
        </div>
      </div>
    </fieldset>
  )
}
