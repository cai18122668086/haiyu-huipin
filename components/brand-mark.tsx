import { DisplayImage } from "./display-image";
import styles from "./brand-mark.module.css";

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`brand-lockup ${styles.lockup} ${compact ? styles.compact : ""}`}
      aria-label="海宇绘品"
    >
      {/* Display derivatives preserve the approved transparent artwork; original files remain intact. */}
      <DisplayImage
        className={styles.artwork}
        src={compact ? "/brand/haiyu-icon-folded-h.png" : "/brand/haiyu-logo-folded-h.png"}
        sizes={compact ? "32px" : "(max-width: 480px) 100px, 142px"}
        width={compact ? 1254 : 2203}
        height={compact ? 1254 : 714}
        alt="海宇绘品"
        draggable={false}
      />
    </div>
  );
}

