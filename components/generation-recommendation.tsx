import { Star } from "lucide-react";

import styles from "./generation-recommendation.module.css";

export function GenerationRecommendation() {
  return <span className={styles.badge}><Star size={11} aria-hidden="true" fill="currentColor" />推荐</span>;
}
