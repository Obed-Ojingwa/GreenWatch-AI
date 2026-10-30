import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, useAccessKey } from '../services/api'
import { AccessKeyButton } from '../components/Layout'
import ImageField, { emptyDraft, type ImageDraft } from '../components/ImageField'
import { ErrorNote, Notice } from '../components/Status'

const num = (s: string) => (s.trim() === '' ? null : Number(s))

export default function NewInvestigation() {
  const key = useAccessKey()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [title, setTitle] = useState('')
  const [location, setLocation] = useState('')
  const [lat, setLat] = useState('')
  const [lng, setLng] = useState('')
  const [objective, setObjective] = useState('Investigate possible change between the earlier and newer image, measure it, and note any limitations.')
  const [notes, setNotes] = useState('')
  const [earlier, setEarlier] = useState<ImageDraft>(emptyDraft)
  const [newer, setNewer] = useState<ImageDraft>(emptyDraft)
  const [createdId, setCreatedId] = useState<string | null>(null)

  const submit = useMutation({
    mutationFn: async () => {
      const inv = createdId
        ? { id: createdId }
        : await api.createInvestigation({
            title: title.trim(), location_name: location.trim(), objective: objective.trim(),
            latitude: num(lat), longitude: num(lng), notes: notes.trim() || null,
          })
      setCreatedId(inv.id)
      await api.uploadImage(inv.id, 'earlier', earlier.file as File, earlier.date, earlier.source.trim())
      await api.uploadImage(inv.id, 'newer', newer.file as File, newer.date, newer.source.trim())
      return inv.id
    },
    onSuccess: (id) => {
      qc.invalidateQueries({ queryKey: ['investigations'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      nav(`/investigations/${id}`)
    },
  })

  const ready = title.trim() && location.trim() && objective.trim() && earlier.file && newer.file && !submit.isPending

  if (!key) {
    return (
      <div className="max-w-xl space-y-4">
        <h1 className="text-2xl font-bold">New investigation</h1>
        <Notice>Creating an investigation needs the access key. Without it you can still open the public sample.</Notice>
        <AccessKeyButton />
      </div>
    )
  }

  return (
    <form
      className="max-w-4xl space-y-6"
      onSubmit={(e) => {
        e.preventDefault()
        if (ready) submit.mutate()
      }}
    >
      <div>
        <h1 className="text-2xl font-bold">New investigation</h1>
        <p className="mt-1 text-ink-soft">Use two images of the same site taken on different dates. Results are most reliable when the images are already aligned.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="title" className="block text-sm font-medium">Investigation title</label>
          <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200}
            className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
        </div>
        <div>
          <label htmlFor="location" className="block text-sm font-medium">Location name</label>
          <input id="location" value={location} onChange={(e) => setLocation(e.target.value)} required maxLength={200}
            placeholder="Neighbourhood or site name" className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
        </div>
        <div>
          <label htmlFor="lat" className="block text-sm font-medium">Latitude (optional)</label>
          <input id="lat" inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
        </div>
        <div>
          <label htmlFor="lng" className="block text-sm font-medium">Longitude (optional)</label>
          <input id="lng" inputMode="decimal" value={lng} onChange={(e) => setLng(e.target.value)} className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
        </div>
      </div>

      <div>
        <label htmlFor="objective" className="block text-sm font-medium">What should the analysis look into?</label>
        <textarea id="objective" value={objective} onChange={(e) => setObjective(e.target.value)} required maxLength={1000} rows={3}
          className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
      </div>
      <div>
        <label htmlFor="notes" className="block text-sm font-medium">Notes (optional)</label>
        <textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} rows={2}
          className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <ImageField role="earlier" value={earlier} onChange={setEarlier} />
        <ImageField role="newer" value={newer} onChange={setNewer} />
      </div>

      <ErrorNote error={submit.error} />
      {createdId && submit.error && (
        <p className="text-sm">
          The investigation was created. You can fix the image and retry here, or{' '}
          <Link className="underline" to={`/investigations/${createdId}`}>open it</Link>.
        </p>
      )}

      <button type="submit" disabled={!ready} className="rounded-ui bg-contour px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">
        {submit.isPending ? 'Uploading…' : 'Create investigation'}
      </button>
    </form>
  )
}
