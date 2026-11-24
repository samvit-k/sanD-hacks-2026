import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import WorkspacePanel from './WorkspacePanel.jsx'

afterEach(cleanup)

const GRAPH = {
  nodes: [],
  papers: {
    'paper-a': { id: 'paper-a', title: 'AI tutor RCT', access_level: 'full', is_new: false },
    'paper-d': { id: 'paper-d', title: 'Retention study', access_level: 'full', is_new: true },
  },
}
const WORKSPACE = { question: 'Do AI tutors improve learning?', counts: { papers: 2, claims: 5, relationships: 3, library_papers: 1 } }

function panel(props = {}) {
  return render(
    <WorkspacePanel graph={GRAPH} workspace={WORKSPACE} selection={null} refs={{}} onSelectPaper={() => {}}
      onSelectQuestion={() => {}} collapsed={false} onToggle={() => {}} projectTitle="AI tutors"
      onClearPapers={vi.fn(async () => {})} onRemovePaper={vi.fn(async () => {})} canClear {...props} />,
  )
}

describe('WorkspacePanel — clearing papers', () => {
  it('"Clear All Papers" states exactly what is deleted (including the chat) and runs only after confirming', async () => {
    const onClearPapers = vi.fn(async () => {})
    panel({ onClearPapers })
    fireEvent.click(screen.getByRole('button', { name: 'Clear All Papers' }))
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain('2 papers')
    expect(dialog.textContent).toContain('5 claims')
    expect(dialog.textContent).toContain('this project\'s chat')
    expect(dialog.textContent).toContain('The central question is kept')
    expect(onClearPapers).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Clear All Papers' }))
    await vi.waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(onClearPapers).toHaveBeenCalledTimes(1)
  })

  it('each paper has its own remove button bound to that paper', async () => {
    const onRemovePaper = vi.fn(async () => {})
    panel({ onRemovePaper })
    fireEvent.click(screen.getByRole('button', { name: 'Remove “Retention study” from this project' }))
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain('Retention study')
    expect(dialog.textContent).toContain('including its impact analysis')
    expect(dialog.textContent).toMatch(/marked “Evidence removed”/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove paper' }))
    await vi.waitFor(() => expect(onRemovePaper).toHaveBeenCalledTimes(1))
    expect(onRemovePaper.mock.calls[0][0]).toMatchObject({ id: 'paper-d', title: 'Retention study' })
  })

  it('cancel changes nothing', () => {
    const onRemovePaper = vi.fn()
    panel({ onRemovePaper })
    fireEvent.click(screen.getByRole('button', { name: 'Remove “AI tutor RCT” from this project' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(onRemovePaper).not.toHaveBeenCalled()
  })

  it('all clearing controls are disabled while work runs, and absent offline or without papers', () => {
    const { unmount } = panel({ canClear: false })
    expect(screen.getByRole('button', { name: 'Clear All Papers' }).disabled).toBe(true)
    for (const b of screen.getAllByRole('button', { name: /^Remove “/ })) expect(b.disabled).toBe(true)
    unmount()
    const { unmount: u2 } = panel({ onClearPapers: null, onRemovePaper: null })
    expect(screen.queryByRole('button', { name: 'Clear All Papers' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Remove “/ })).toBeNull()
    u2()
    panel({ graph: { nodes: [], papers: {} } })
    expect(screen.queryByRole('button', { name: 'Clear All Papers' })).toBeNull()
  })

  it('a refused clear (e.g. analysis running in another tab) keeps the dialog open with the reason', async () => {
    const onClearPapers = vi.fn(async () => { throw new Error('"Paper X" is being analyzed right now.') })
    panel({ onClearPapers })
    fireEvent.click(screen.getByRole('button', { name: 'Clear All Papers' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Clear All Papers' }))
    expect((await screen.findByRole('alert')).textContent).toContain('being analyzed')
    expect(screen.getByRole('alertdialog')).toBeTruthy()
  })
})