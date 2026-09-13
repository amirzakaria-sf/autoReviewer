export function StatusBadge({ label, color }: { label: string; color: string }) {
  return <span className={`badge badge-${color}`}>{label}</span>;
}
