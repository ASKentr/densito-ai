export type Role = "admin" | "expert";

export type StudyStatus = "uploaded" | "processing" | "analyzed" | "error";
export type ReviewStatus = "not_reviewed" | "in_review" | "reviewed";
export type OverallVerdict = "qualitative" | "non_qualitative";
export type Severity = "low" | "medium" | "high" | "critical";
export type FindingStatus = "pending" | "confirmed" | "rejected" | "modified" | "added";
export type FindingSource = "ai" | "expert";

export interface UserOut {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  is_active: boolean;
}

export interface ViolationType {
  id: number;
  code: string;
  name_ru: string;
  category: string;
  description: string;
  default_severity: Severity;
  is_active: boolean;
  is_system: boolean;
}

export interface FindingOut {
  id: number;
  image_id: number;
  violation_type_id: number;
  violation_code: string;
  violation_name: string;
  source: FindingSource;
  status: FindingStatus;
  severity: Severity;
  confidence: number | null;
  bbox_x: number | null;
  bbox_y: number | null;
  bbox_w: number | null;
  bbox_h: number | null;
  comment: string;
  reviewed_by: string | null;
}

export interface ImageOut {
  id: number;
  filename: string;
  rows: number | null;
  columns: number | null;
  frame_index: number;
}

export interface StudyListItem {
  id: number;
  display_id: string;
  study_date: string | null;
  body_part: string | null;
  study_description: string | null;
  modality: string | null;
  status: StudyStatus;
  review_status: ReviewStatus;
  overall_verdict: OverallVerdict | null;
  overall_score: number | null;
  findings_count: number;
  anonymization_ok: boolean | null;
  created_at: string;
}

export interface StudyDetail extends StudyListItem {
  original_filename: string;
  patient_pseudo_id: string;
  anonymization_report: { issues: { file: string; found_phi: { tag: string; label: string; value: string }[] }[] };
  images: ImageOut[];
  findings: FindingOut[];
  model_version: string | null;
}

export interface AuditLogEntryOut {
  id: number;
  username: string;
  action: string;
  entity: string;
  entity_id: number | null;
  details: string;
  created_at: string;
}

export interface StatsOut {
  total_studies: number;
  analyzed_studies: number;
  qualitative: number;
  non_qualitative: number;
  reviewed: number;
  pending_review: number;
  findings_by_category: Record<string, number>;
}
