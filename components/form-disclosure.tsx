"use client";

import { useId, useState, type ReactNode } from "react";
import { ChevronDown, SlidersHorizontal } from "lucide-react";

/** Keep optional inputs mounted so collapsing a group never clears a draft. */
export function FormDisclosure({ title, summary, children, defaultOpen = false }: {
  title: string;
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return <section className="creation-disclosure" data-open={open || undefined}>
    <button type="button" className="creation-disclosure__trigger" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>
      <SlidersHorizontal size={16} aria-hidden="true" />
      <span><strong>{title}</strong><small>{summary}</small></span>
      <ChevronDown size={16} aria-hidden="true" />
    </button>
    <div id={id} className="creation-disclosure__content" inert={!open}>
      <div><div className="creation-disclosure__fields">{children}</div></div>
    </div>
  </section>;
}
