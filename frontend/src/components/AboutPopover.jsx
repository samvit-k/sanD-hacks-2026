import { useEffect, useState } from 'react'

export const TEAM = [
  {
    name: 'Dhruv Madan',
    email: 'dhruvmadan14@gmail.com',
    bio: 'Dhruv is a Computer Science major at UC San Diego who enjoys building a wide range of projects, with a particular interest in robotics.',
  },
  {
    name: 'Samvit Kundapur',
    email: 'skundapur@ucsd.edu',
    bio: 'Samvit is a Computer Science major at UC San Diego focused on AI engineering projects, and hopes to explore robotics and machine learning further.',
  },
  {
    name: 'Aakarsh Jha',
    email: 'aakarsh.jha08@gmail.com',
    bio: 'Aakarsh is a Computer Science student at UC San Diego who works on AI and machine learning projects, with the goal of building deeper expertise in AI and its applications.',
  },
]

/** A contact button in the top bar that opens an "About Us" popover; each name opens an email to that person. */
export default function AboutPopover() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="filter about">
      <button type="button" className={`btn btn-icon ${open ? 'btn-on' : ''}`} onClick={() => setOpen((v) => !v)}
        aria-expanded={open} aria-haspopup="dialog" aria-label="About us" title="About Us">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6" />
          <path d="M8 7.2v3.6" />
          <circle cx="8" cy="5.2" r="0.45" fill="currentColor" />
        </svg>
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="popover about-panel" role="dialog" aria-label="About us" data-testid="about">
            <div className="about-head">
              <img src="/logo.svg" alt="" />
              <div className="pop-title">About Us</div>
            </div>
            {TEAM.map((m) => (
              <div className="about-person" key={m.email}>
                <a className="about-name" href={`mailto:${m.email}`} title={`Email ${m.name}`}>{m.name}</a>
                <p>{m.bio}</p>
                <a className="about-email" href={`mailto:${m.email}`}>{m.email}</a>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
