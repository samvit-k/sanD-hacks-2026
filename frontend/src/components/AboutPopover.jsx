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
        aria-expanded={open} aria-haspopup="dialog" aria-label="About us" title="About Us · Contact">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M5.2 2.5 3.6 2.6c-.7.1-1.2.7-1.1 1.4.5 5 4.5 9 9.5 9.5.7.1 1.3-.4 1.4-1.1l.1-1.6c0-.4-.2-.7-.6-.9l-2-.9c-.3-.1-.7 0-.9.2l-.8 1c-1.6-.8-2.9-2.1-3.7-3.7l1-.8c.2-.2.3-.6.2-.9l-.9-2c-.2-.4-.5-.6-.9-.6z" />
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
