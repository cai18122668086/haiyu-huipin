import styles from "./brand-mark.module.css";

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`brand-lockup ${styles.lockup} ${compact ? styles.compact : ""}`}
      aria-label="海宇绘品"
    >
      {/* The approved transparent artwork includes the wordmark and needs no image transformation. */}
      <img
        className={styles.artwork}
        src={compact ? "/brand/haiyu-icon-folded-h.png" : "/brand/haiyu-logo-folded-h.png"}
        width={compact ? 1254 : 2203}
        height={compact ? 1254 : 714}
        alt="海宇绘品"
        draggable={false}
      />
    </div>
  );
}

