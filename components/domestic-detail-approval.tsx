"use client";

import type { ReactNode } from "react";

export const NATIVE_DETAIL_APPROVAL_TEXT = "我已审阅上述完整方案，同意按此版本逐组生成。";

function cells(line: string): string[] {
  return line.split("|").slice(1, -1).map(value => value.trim());
}

// Render the compiler's entire document as text and tables. Model-authored
// HTML is always escaped by React; no raw HTML, script or remote image executes.
export function NativeApprovalDocument({ document, label = "完整逐屏方案" }: { document: string; label?: string }) {
  const lines = document.split(/\r?\n/);
  const blocks: ReactNode[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) continue;
    if (line.startsWith("|") && lines[index + 1]?.startsWith("|") && cells(lines[index + 1]).every(value => /^:?-{2,}:?$/.test(value))) {
      const headers = cells(line);
      const rows: string[][] = [];
      const start = index;
      index += 2;
      while (index < lines.length && lines[index].startsWith("|")) rows.push(cells(lines[index++]));
      index -= 1;
      blocks.push(<div key={start} style={{ overflowX: "auto", marginBlock: 20 }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: Math.min(1200, headers.length * 150), fontSize: 13 }}>
          <thead><tr>{headers.map((header, column) => <th key={column} style={{ textAlign: "left", padding: 10, border: "1px solid #ddd", verticalAlign: "top" }}>{header}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, column) => <td key={column} style={{ padding: 10, border: "1px solid #ddd", verticalAlign: "top", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{value}</td>)}</tr>)}</tbody>
        </table>
      </div>);
    } else {
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      blocks.push(heading
        ? <h3 key={index} style={{ marginBlock: "24px 12px", fontSize: heading[1].length <= 2 ? 18 : 16 }}>{heading[2]}</h3>
        : <p key={index} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", lineHeight: 1.8 }}>{line}</p>);
    }
  }
  return <div aria-label={label}>{blocks}</div>;
}

export function DomesticDetailApproval({ document, fingerprint, reviewed, onReviewed, description = "请审阅商品事实、每屏内容、构图及双屏衔接，再确认开始生成。" }: {
  document: string; fingerprint: string; reviewed: boolean; onReviewed: (reviewed: boolean) => void;
  description?: string;
}) {
  return <section className="detail-form-card" aria-label="完整详情方案审批">
    <div className="form-section-head"><div><h2>完整详情方案</h2><p>{description}</p></div></div>
    <NativeApprovalDocument document={document} />
    <p style={{ overflowWrap: "anywhere", fontSize: 12 }}>方案指纹：{fingerprint}</p>
    <p><a download="detail-approval.md" href={`data:text/markdown;charset=utf-8,${encodeURIComponent(document)}`}>下载完整方案</a></p>
    <label><input type="checkbox" style={{ width: "auto", marginRight: 8 }} checked={reviewed} onChange={event => onReviewed(event.target.checked)} />{NATIVE_DETAIL_APPROVAL_TEXT}</label>
  </section>;
}
