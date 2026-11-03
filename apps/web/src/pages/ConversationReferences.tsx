import type { ConversationReference } from "../types";

type ConversationReferencesProps = {
  references: ConversationReference[];
  onReference: (reference: ConversationReference) => void;
};

function referencePrefix(reference: ConversationReference) {
  if (reference.type === "source") return "来源";
  if (reference.type === "web") return "网页";
  return "需求";
}

function webReferenceUrl(reference: ConversationReference) {
  const value = reference.url ?? reference.id;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function referenceTitle(reference: ConversationReference) {
  const title = reference.title?.trim();
  if (title) return title;

  if (reference.type === "web") {
    const url = webReferenceUrl(reference);
    if (url) {
      try {
        return new URL(url).hostname || "网页来源";
      } catch {
        return "网页来源";
      }
    }
    return "网页来源";
  }

  if (reference.id !== undefined && reference.id !== null) return String(reference.id);
  return reference.type === "source" ? "来源详情" : "需求详情";
}

export function ConversationReferences({
  references,
  onReference,
}: ConversationReferencesProps) {
  if (!references.length) return null;

  return (
    <div className="reference-list">
      {references.map((reference, index) => {
        const prefix = referencePrefix(reference);
        const title = referenceTitle(reference);
        const content = (
          <>
            <span className="reference-prefix">{prefix}：</span>
            <span className="reference-title">{title}</span>
          </>
        );
        const key = `${reference.type ?? "requirement"}-${String(reference.id ?? "")}-${index}`;

        if (reference.type === "web") {
          const href = webReferenceUrl(reference);
          return href ? (
            <a
              className="reference-item"
              href={href}
              key={key}
              rel="noopener noreferrer"
              target="_blank"
              title={title}
            >
              {content}
            </a>
          ) : (
            <span className="reference-item" key={key} title={title}>
              {content}
            </span>
          );
        }

        return (
          <button
            className="reference-item"
            key={key}
            onClick={() => onReference(reference)}
            title={title}
            type="button"
          >
            {content}
          </button>
        );
      })}
    </div>
  );
}
