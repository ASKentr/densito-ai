// Общие элементы интерфейса: иконки (inline SVG, без внешних загрузок),
// логотип, словари подписей и форматирование.

const PATHS: Record<string, string> = {
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  back: "m15 18-6-6 6-6",
  play: "M6 4l14 8-14 8V4Z",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36L21 8M21 3v5h-5",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm0 0v6h6M9 13h6M9 17h6",
  archive: "M21 8v13H3V8M1 3h22v5H1zM10 12h4",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Zm-3-10 2 2 4-4",
  layers: "m12 2 10 5-10 5L2 7l10-5Zm-10 10 10 5 10-5M2 17l10 5 10-5",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-14v4l3 2",
  square: "M4 4h16v16H4z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

/** Знак: концентрические дуги — «плотность» + срез кости. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }}>
      <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth={2.2} strokeLinecap="round" aria-hidden="true">
        <path d="M12 3a9 9 0 1 0 9 9" />
        <path d="M12 7.5a4.5 4.5 0 1 0 4.5 4.5" />
        <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      </svg>
    </span>
  );
}

export const STATUS_LABELS: Record<string, string> = {
  uploaded: "Загружено", processing: "Обрабатывается", analyzed: "Проанализировано", error: "Ошибка",
};
export const REVIEW_LABELS: Record<string, string> = {
  not_reviewed: "Не проверено", in_review: "На проверке", reviewed: "Проверено экспертом",
};
export const VERDICT_LABELS: Record<string, string> = {
  qualitative: "Качественное", non_qualitative: "Есть нарушения",
};
export const CATEGORY_LABELS: Record<string, string> = {
  positioning: "Укладка и позиционирование", field_of_view: "Область исследования", artifact: "Артефакты",
  image_quality: "Качество изображения", roi_segmentation: "ROI и сегментация", dicom_technical: "Технические ошибки DICOM",
  phi: "Обезличивание",
};
export const ACTION_LABELS: Record<string, string> = {
  login: "Вход в систему", upload_study: "Загрузка исследования", run_analysis: "ИИ-анализ",
  finding_confirm: "Находка подтверждена", finding_reject: "Находка отклонена", finding_modify: "Находка изменена",
  finding_added_by_expert: "Находка эксперта", complete_review: "Проверка завершена",
  batch_process: "Пакетная обработка", export_sr: "Выгрузка DICOM SR", create_user: "Создан пользователь",
  deactivate_user: "Пользователь отключён", create_violation_type: "Новый тип нарушения",
  update_violation_type: "Изменён тип нарушения", delete_violation_type: "Удалён тип нарушения",
};

export function formatDicomDate(d?: string | null): string {
  // обезличенные файлы вместо даты содержат заглушку («Anonymized») — показываем прочерк
  if (!d || !/^\d{8}$/.test(d)) return "—";
  return `${d.slice(6, 8)}.${d.slice(4, 6)}.${d.slice(0, 4)}`;
}

/** Цвет шкалы вероятности нарушения: зелёный → янтарный → красный. */
export function probColor(p: number): string {
  if (p < 0.3) return "var(--ok)";
  if (p < 0.6) return "#d97706";
  return "var(--bad)";
}

export function ProbMeter({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="muted">—</span>;
  return (
    <div className="prob-meter" title="Вероятность нарушения качества по оценке ИИ">
      <div className="prob-track"><div className="prob-fill" style={{ width: `${Math.round(value * 100)}%`, background: probColor(value) }} /></div>
      <span className="prob-value">{value.toFixed(2)}</span>
    </div>
  );
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}
