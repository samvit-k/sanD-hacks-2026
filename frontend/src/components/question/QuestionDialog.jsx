// Central research question: describe an interest → Gemini suggests → review / edit / regenerate → confirm.
// Gemini only suggests; the question is saved solely by the "Confirm" button (and, for a project that already
// has papers, by an explicit choice about what happens to the existing analysis).
import { useEffect, useRef, useState } from 'react'
import { MAX_QUESTION_LEN, ORIGIN, useQuestionComposer } from '../../state/useQuestionComposer.js'
import { plural } from '../../text.js'

const ORIGIN_LABEL = {
  [ORIGIN.gemini]: 'Suggested by Gemini — review before confirming',
  [ORIGIN.edited]: 'Gemini suggestion, edited by you',
  [ORIGIN.manual]: 'Written by you',
}

function ConflictPanel({ composer, onCancel }) {
  const { conflict, confirming } = composer
  const [resetStep, setResetStep] = useState(false)
  const c = conflict.counts ?? {}
  return (
    <div className="qd-conflict" role="alertdialog" aria-labelledby="qd-conflict-title">
      <h3 id="qd-conflict-title">This project already has evidence analyzed for a different question</h3>
      <dl className="qd-compare">
        <div><dt>Current question</dt><dd>{conflict.current_question}</dd></div>
        <div><dt>New question</dt><dd>{conflict.question}</dd></div>
      </dl>
      <p>
        Claims were extracted, and relationships and impacts judged, specifically for the current question, and paper
        relevance is measured against it. They are <b>not</b> reinterpreted under a new question.
      </p>
      {!resetStep ? (
        <>
          <div className="qd-option">
            <div className="qd-option-title">Keep papers, re-analyze under the new question</div>
            <ul>
              <li><b>Kept:</b> {plural(c.papers ?? 0, 'paper')} with their full text, their Node numbers, and the chat history (shown as asked under the previous question).</li>
              <li><b>Cleared:</b> {plural(c.claims ?? 0, 'claim')}, {plural(c.relationships ?? 0, 'relationship')}, {plural(c.impacts ?? 0, 'impact')}.</li>
              <li><b>Then:</b> you start re-analysis yourself, paper by paper (Gemini calls; usually 20–80 s per paper). New claims get new Node numbers.</li>
            </ul>
          </div>
          <div className="qd-actions">
            <button type="button" className="btn btn-sm" onClick={onCancel} disabled={confirming}>Keep Current Question</button>
            <button type="button" className="btn btn-sm btn-primary" disabled={confirming}
              onClick={() => composer.confirm('keep_papers')}>
              {confirming ? 'Saving…' : 'Clear Analysis and Use New Question'}
            </button>
          </div>
          <button type="button" className="link-btn muted qd-reset-link" disabled={confirming} onClick={() => setResetStep(true)}>
            Start Over Without These Papers…
          </button>
        </>
      ) : (
        <div className="qd-danger">
          <p>
            This permanently deletes {plural(c.papers ?? 0, 'paper')} and their text, {plural(c.claims ?? 0, 'claim')},{' '}
            {plural(c.relationships ?? 0, 'relationship')}, {plural(c.impacts ?? 0, 'impact')}, all Node numbers and the chat
            history. It cannot be undone.
          </p>
          <div className="qd-actions">
            <button type="button" className="btn btn-sm" onClick={() => setResetStep(false)} disabled={confirming}>Back</button>
            <button type="button" className="btn btn-sm btn-danger" disabled={confirming} onClick={() => composer.confirm('reset')}>
              {confirming ? 'Deleting…' : `Delete ${plural(c.papers ?? 0, 'Paper')} and Use New Question`}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function QuestionDialog({ open, client, workspace, onClose, onConfirmed }) {
  const current = workspace?.question ?? null
  const composer = useQuestionComposer({ client, currentQuestion: current, onConfirmed })
  const { mode, setMode, interest, setInterest, latest, draft, setDraft, origin, generating, error, conflict } = composer
  const storedPapers = workspace?.counts?.papers ?? 0
  const picked = composer.paperFiles
  const [drag, setDrag] = useState(false)
  const fileRef = useRef(null)
  const interestRef = useRef(null)
  const draftRef = useRef(null)
  const busy = generating || composer.confirming

  useEffect(() => {
    if (!open) return undefined
    const t = setTimeout(() => (mode === 'manual' ? draftRef : interestRef).current?.focus(), 30)
    const onKey = (e) => { if (e.key === 'Escape' && !composer.confirming) { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', onKey, true)
    return () => { clearTimeout(t); window.removeEventListener('keydown', onKey, true) }
  }, [open, mode, onClose, composer.confirming])

  if (!open) return null
  const addFiles = (list) => {
    const pdfs = Array.from(list ?? []).filter((f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name))
    const fresh = pdfs.filter((f) => !picked.some((x) => x.name === f.name && x.size === f.size))
    if (fresh.length) composer.setPaperFiles([...picked, ...fresh])
  }
  const showDraft = mode === 'manual' || !!latest || !!draft
  const tooLong = draft.trim().length > MAX_QUESTION_LEN

  return (
    <div className="qd-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <div className="qd" role="dialog" aria-modal="true" aria-labelledby="qd-title">
        <div className="qd-head">
          <h2 id="qd-title">{current ? 'Change the Central Question' : 'Set the Central Research Question'}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" disabled={composer.confirming}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </button>
        </div>
        <div className="qd-body">
          {current && <p className="qd-current"><span>Current:</span> {current}</p>}
          <p className="qd-intro">
            The central question grounds claim extraction, comparison and impact analysis. Nothing is saved until you confirm.
          </p>

          <div className="seg" role="radiogroup" aria-label="How to write the question">
            {[['interest', 'Describe Your Interest'], ['papers', 'From My Papers'], ['manual', 'Write It Myself']].map(([key, label]) => (
              <button key={key} type="button" role="radio" aria-checked={mode === key}
                className={`seg-btn ${mode === key ? 'on' : ''}`} onClick={() => setMode(key)}>{label}</button>
            ))}
          </div>

          {mode === 'interest' && (
            <div className="qd-field">
              <label htmlFor="qd-interest">Your Research Interest <span className="qd-sub">(your own words)</span></label>
              <textarea id="qd-interest" ref={interestRef} rows={3} maxLength={1000} value={interest}
                placeholder="e.g. I'm interested in how AI tutoring affects student learning outcomes."
                onChange={(e) => setInterest(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) composer.generate() }} />
              <div className="qd-row">
                <button type="button" className="btn btn-sm" disabled={!interest.trim() || busy}
                  onClick={() => (latest ? composer.regenerate() : composer.generate())}>
                  {generating ? <><span className="spinner" aria-hidden="true" /> Suggesting…</> : latest ? 'Regenerate Suggestion' : 'Suggest a Question'}
                </button>
                {generating && <button type="button" className="link-btn muted" onClick={composer.cancelGeneration}>Cancel</button>}
                <span className="qd-hint">Gemini proposes a question only; it does not look up or state any findings.</span>
              </div>
            </div>
          )}

          {mode === 'papers' && (
            <div className="qd-field">
              <span className="qd-label">Your Papers <span className="qd-sub">(PDFs; the first pages are read)</span></span>
              <div className={`drop ${drag ? 'drag' : ''} ${busy ? 'disabled' : ''}`}
                onDragOver={(e) => { e.preventDefault(); if (!busy) setDrag(true) }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); if (!busy) addFiles(e.dataTransfer.files) }}>
                <button type="button" className="btn btn-sm" disabled={busy} onClick={() => fileRef.current?.click()}>
                  Choose PDFs…
                </button>
                <span className="drop-hint">
                  {picked.length ? 'or drop more PDF files here'
                    : storedPapers > 0 ? `or leave empty to use the ${plural(storedPapers, 'paper')} in this project`
                      : 'or drop PDF files here'}
                </span>
                <input ref={fileRef} type="file" accept="application/pdf,.pdf" multiple hidden data-testid="qd-pdf-input"
                  onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />
              </div>
              {picked.length > 0 && (
                <ul className="qd-files">
                  {picked.map((f) => (
                    <li key={`${f.name}:${f.size}`}>
                      <span className="qd-file-name">{f.name}</span>
                      <button type="button" className="icon-btn qd-file-remove" aria-label={`Remove ${f.name}`} disabled={busy}
                        onClick={() => composer.setPaperFiles(picked.filter((x) => x !== f))}>
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {picked.length > 0 && (
                <p className="qd-hint">After you confirm the question, {picked.length === 1 ? 'this paper is' : 'these papers are'} added to the evidence library.</p>
              )}
              <div className="qd-row">
                <button type="button" className="btn btn-sm" disabled={busy || (!picked.length && !storedPapers)}
                  onClick={() => (latest ? composer.regenerate() : composer.generate())}>
                  {generating ? <><span className="spinner" aria-hidden="true" /> Reading Papers…</> : latest ? 'Regenerate Suggestion' : 'Suggest a Question'}
                </button>
                {generating && <button type="button" className="link-btn muted" onClick={composer.cancelGeneration}>Cancel</button>}
                <span className="qd-hint">Gemini proposes a question the papers share; it does not state their findings.</span>
              </div>
              {latest?.papers?.length > 0 && origin !== ORIGIN.manual && (
                <p className="qd-hint">Based on: {latest.papers.join('; ')}</p>
              )}
            </div>
          )}

          {error && error.kind !== 'after_save' && <div className="warn-card" role="alert">{error.message}</div>}

          {showDraft && (
            <div className={`qd-field qd-draft ${origin === ORIGIN.manual ? '' : 'is-model'}`}>
              <label htmlFor="qd-draft">Central Research Question</label>
              {origin && <div className={`qd-origin origin-${origin}`} data-testid="question-origin">{ORIGIN_LABEL[origin]}</div>}
              <textarea id="qd-draft" ref={draftRef} rows={3} value={draft} disabled={composer.confirming}
                placeholder="e.g. How does AI-assisted tutoring influence student learning outcomes compared with traditional instruction?"
                onChange={(e) => setDraft(e.target.value)} />
              {latest?.rationale && origin !== ORIGIN.manual && (
                <p className="qd-rationale"><span>Why this wording (Gemini):</span> {latest.rationale}</p>
              )}
              {composer.keptEdits && (
                <p className="qd-note" role="status">
                  A new suggestion arrived while you were editing; your text was kept.{' '}
                  <button type="button" className="link-btn" onClick={() => composer.pickProposal()}>Use the Suggestion</button>
                </p>
              )}
              {origin === ORIGIN.edited && latest && (
                <button type="button" className="link-btn muted" onClick={composer.revert}>Revert to Gemini's Suggestion</button>
              )}
              <p className="qd-hint">
                Check that it matches your intended scope — automatic checks catch obvious added restrictions (years, places,
                age groups), not every nuance.
              </p>
              {tooLong && <p className="qd-hint warn">Keep the question under {MAX_QUESTION_LEN} characters.</p>}
              {composer.unchanged && draft.trim() && <p className="qd-hint">This is already the central question.</p>}
            </div>
          )}

          {conflict && <ConflictPanel key={conflict.question} composer={composer} onCancel={composer.cancelConflict} />}
          {error?.kind === 'after_save' && (
            <div className="warn-card" role="alert">
              {error.message}{' '}
              <button type="button" className="link-btn" onClick={composer.retryAfterSave}>Retry</button>
            </div>
          )}
        </div>
        {!conflict && (
          <div className="qd-foot">
            <button type="button" className="btn btn-sm" onClick={onClose} disabled={composer.confirming}>Cancel</button>
            <button type="button" className="btn btn-sm btn-primary" disabled={!composer.canConfirm} onClick={() => composer.confirm()}>
              {composer.confirming ? 'Saving…' : 'Confirm Question'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}